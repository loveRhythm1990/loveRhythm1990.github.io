---
layout:     post
title:      "Local volume provisioner架构分析"
date:       2020-07-18 03:10:00
author:     "weak old dog"
header-img-credit: false
tags:
    - k8s
    - 存储
---

local volume provisioner，一个提供本地存储的组件，主要是不用管理员手动创建pv了，可以根据目录自动发现并创建pv，梳理一下其工作过程。

#### main函数
main函数做的工作不多，就是启动controller，并注册了一些prometheus监控项。
```go
	go controller.StartLocalController(client, procTable, &common.UserConfig{
		Node:                    node,
        // 忽略其他代码
	})
	prometheus.MustRegister([]prometheus.Collector{
		metrics.PersistentVolumeDiscoveryTotal,
        // 忽略其他代码
	}...)
```

#### Local Controller 主流程
`StartLocalController`主要做两件事：pv的发现和删除。此外，还维护了一份pv的缓存，以及通过一个job来辅助删除pod。先列出整个流程，然后逐步分析
```go
func StartLocalController(client *kubernetes.Clientset, ptable deleter.ProcTable, config *common.UserConfig) {
    // 生成pv缓存
	populator.NewPopulator(runtimeConfig)
    // 根据配置决定是否启用job controller
	var jobController deleter.JobController
	if runtimeConfig.UseJobForCleaning {
		jobController, err = deleter.NewJobController(labels, runtimeConfig)
	}
	cleanupTracker := &deleter.CleanupStatusTracker{ProcTable: ptable, JobController: jobController}
    // 发现
	discoverer, err := discovery.NewDiscoverer(runtimeConfig, cleanupTracker)
    // 删除
	deleter := deleter.NewDeleter(runtimeConfig, cleanupTracker)
	// Run controller logic.
	if jobController != nil {
		go jobController.Run(wait.NeverStop)
	}
	for {
		deleter.DeletePVs()
		discoverer.DiscoverLocalVolumes()
		time.Sleep(10 * time.Second)
	}
}
```
流程简单清晰，现在逐步看一下。

##### 生成pv缓存
这个本质上没什么好说的，就是通过informer机制来同步缓存。这部分逻辑如下。
```go
func NewPopulator(config *common.RuntimeConfig) *Populator {
    p := &Populator{RuntimeConfig: config}
    // 定制化一个listwatch，只缓存label值等于value的pv
	optionsModifier := func(options *metav1.ListOptions) {
		options.LabelSelector = apis.LabelHostname + "=" + config.Node.Name
	}
    // 使用factory生成一个informer，对于同一种类型，可以调用多次InformerFor，但是factory只会生成一个，后续会返回之前生成的
	pvInformer := config.InformerFactory.InformerFor(
		&v1.PersistentVolume{},
		func(client kubernetes.Interface, resyncPeriod time.Duration) cache.SharedIndexInformer {
			return cache.NewSharedIndexInformer(
				cache.NewFilteredListWatchFromClient(client.CoreV1().RESTClient(),"persistentvolumes","",optionsModifier,),
				&v1.PersistentVolume{},
				resyncPeriod,
				cache.Indexers{cache.NamespaceIndex: cache.MetaNamespaceIndexFunc},
			)
		},
	)
    // 监听事件，来更新pv缓存，
	pvInformer.AddEventHandler(cache.ResourceEventHandlerFuncs{
		AddFunc: func(obj interface{}) {
			p.handlePVUpdate(pv)        //更新缓存的pv
		},
		UpdateFunc: func(oldObj, newObj interface{}) {
			p.handlePVUpdate(newPV)
		},
		DeleteFunc: func(obj interface{}) {
			p.handlePVDelete(pv)   // 从缓存中删除pv。
		},
	})
	return p
}
```
pv缓存的构建过程就这样结束了。

##### 发现pv
发现过程分两步，先构建一个discoverer，再调用discoverer的`DiscoverLocalVolumes`方法。下面重点看一下后者
```go
func (d *Discoverer) discoverVolumesAtPath(class string, config common.MountConfig) {
    // 从缓存中拿storageclass的回收策略，默认是delete
    reclaimPolicy, err := d.getReclaimPolicyFromStorageClass(class)
	files, err := d.VolUtil.ReadDir(config.MountDir)
	// 获取系统的所有挂载点
	mountPoints, err := d.RuntimeConfig.Mounter.List()
	if err != nil {
		klog.Errorf("Error retreiving mountpoints: %v", err)
		return
	}
	// 将所有挂载点放到一个集合中，便于查找
	type empty struct{}
	mountPointMap := make(map[string]empty)
	for _, mp := range mountPoints {
		mountPointMap[mp.Path] = empty{}
	}

	for _, file := range files {
		startTime := time.Now()
        filePath := filepath.Join(config.MountDir, file)
        // 判断是块设备还是文件系统
		volMode, err := common.GetVolumeMode(d.VolUtil, filePath)
		// 查看pv是否存在，特定目录生成的pv名字是固定的
		pvName := generatePVName(file, d.Node.Name, class)
		pv, exists := d.Cache.GetPV(pvName)
		if exists {
			continue
		}
		usejob := false
		if volMode == v1.PersistentVolumeBlock {
			usejob = d.RuntimeConfig.UseJobForCleaning
        }
        // pv正在被删除，暂时不创建
		if d.CleanupTracker.InProgress(pvName, usejob) {
			klog.Infof("PV %s is still being cleaned, not going to recreate it", pvName)
			continue
		}
		// cleanup old status，忽略错误
		d.CleanupTracker.RemoveStatus(pvName, usejob)

		mountOptions, err := d.getMountOptionsFromStorageClass(class)
		if err != nil {
			klog.Errorf("Failed to get mount options from storage class %s: %v", class, err)
			continue
		}

		var capacityByte int64
		desireVolumeMode := v1.PersistentVolumeMode(config.VolumeMode)
		switch volMode {
		case v1.PersistentVolumeBlock:
			capacityByte, err = d.VolUtil.GetBlockCapacityByte(filePath)  // 查看块设备大小
		case v1.PersistentVolumeFilesystem:
			capacityByte, err = d.VolUtil.GetFsCapacityByte(filePath) // 该目录是挂载点，获取容量
		default:
			klog.Errorf("Path %q has unexpected volume type %q", filePath, volMode)
			continue
		}
        // 调用client-go创建pv
		d.createPV(file, class, reclaimPolicy, mountOptions, config, capacityByte, desireVolumeMode, startTime)
	}
}
```

##### 删除pv
删除pv的过程可能要麻烦一点。首先是从pv缓存中枚举pv，只删除phase状态为Released的pv，其他状态的pv不处理，并且在storageclass的回收策略为Delete的时候才删除。

这里有个疑问，当sc回收策略为delete时，pv是被谁删的？

大概浏览了一下pv controller的代码，如果plugin没有实现deletable接口（local volume plugin没有实现），pv controller是不会删除pv的。所以这里pv的删除工作还是交给local volume provisioner了。具体是成功清除目录里的数据后会删除pv，但是根据pv的发现机制，马上会创建一个同名的pv出来，并且状态为Available。

删除pv时，这里可以通过起一个job来删除，或者直接起一个goroutine，判断的条件是：
```go
func (d *Deleter) shouldRunJob(mode v1.PersistentVolumeMode) bool {
	return mode == v1.PersistentVolumeBlock && d.RuntimeConfig.UseJobForCleaning
}
```
另一方面，删除时，会通过statustracker来记录删除状态。这个一会分析下。这里先看下上面两种删除方式。

**使用goroutine删除**
起一个goroutine来异步删除。
```go
func (d *Deleter) runProcess(pv *v1.PersistentVolume, volMode v1.PersistentVolumeMode, mountPath string,config common.MountConfig) error {
	// Run as exec script.
	err := d.CleanupStatus.ProcTable.MarkRunning(pv.Name)
	go d.asyncCleanPV(pv, volMode, mountPath, config)
	return nil
}
func (d *Deleter) asyncCleanPV(pv *v1.PersistentVolume, volMode v1.PersistentVolumeMode, mountPath string,config common.MountConfig) {
    // 这个是清空目录，不再跟进去了。
	err := d.cleanPV(pv, volMode, mountPath, config)
	if err != nil {
		// 标记为失败
		if err := d.CleanupStatus.ProcTable.MarkFailed(pv.Name); err != nil {
			klog.Error(err)
		}
		return
	}
	// 标记为成功
	if err := d.CleanupStatus.ProcTable.MarkSucceeded(pv.Name); err != nil {
		klog.Error(err)
	}
}
```

**标记pv回收流程**
用来标记pv回收流程的接口和结构体如下：
```go
// ProcTable Interface for tracking running processes
type ProcTable interface {
	// CleanupBlockPV deletes block based PV
	IsRunning(pvName string) bool
	IsEmpty() bool
	MarkRunning(pvName string) error
	MarkFailed(pvName string) error
	MarkSucceeded(pvName string) error
	RemoveEntry(pvName string) (CleanupState, *time.Time, error)
	Stats() ProcTableStats
}
// ProcEntry represents an entry in the proc table
type ProcEntry struct {
	StartTime time.Time
	Status    CleanupState  // state有三种：Running，Succeeded，Failed
}

// ProcTableImpl Implementation of BLockCleaner interface
type ProcTableImpl struct {
	mutex     sync.RWMutex
	procTable map[string]ProcEntry  // 带锁的map
	succeeded int    // 统计success的数目
	failed    int    // 统计failed的数目
}
```
工作流程大概如下：
1. 删除前检查该pv是否正在执行删除任务（pv的删除状态为Running或者存在对应job），如果是，直接返回.
2. 检查当前状态（同时删除success或者failed状态，如果有），如果succeed，就使用client-go删除pv。如果failed或者notExist则开始删除任务。
3. 开始删除时，标记为running。
4. 删除结束，标记为失败或者成功。
5. discover在创建pv时，如果正在执行删除，则不创建，（实际情况可能更复杂一点）

先到这吧，有问题再补充