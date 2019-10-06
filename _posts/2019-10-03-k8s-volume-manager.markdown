---
layout:     post
title:      "Kubelet volume manager"
subtitle:   " \"概述\""
date:       2019-10-04 19:16:00
author:     "weak old dog"
header-img-credit: false
tags:
    - k8s
    - kubelet
---


## 前言
本身是做k8s存储相关工作的，但对kubelet volume-manager怎么工作的，了解的还不够多，这里分析一下吧。基于的k8s版本为1.16。volume-manager的入口函数在`pkg/kubelet/kubelet.go`文件中。程序启动非常简单：
```golang
	// Start volume manager
	go kl.volumeManager.Run(kl.sourcesReady, wait.NeverStop)
```
下面就从这个入口函数开始分析。在贴代码时，会删去代码中的加锁、校验、监控项、错误处理、以及日志等不影响业务逻辑的代码。

## Run方法
volume-manager的主要代码在`pkg/kubelet/volume-manager`这个package中。`Run`方法的实现为：
```golang
func (vm *volumeManager) Run(sourcesReady config.SourcesReady, stopCh <-chan struct{}) {

	go vm.desiredStateOfWorldPopulator.Run(sourcesReady, stopCh)

	go vm.reconciler.Run(stopCh)

	if vm.kubeClient != nil {
		// start informer for CSIDriver
		vm.volumePluginMgr.Run(stopCh)
	}

	<-stopCh
}
```
由此可见，`Run`主要做两件事，启动`desiredStateOfWorldPopulator`以及启动`reconciler`逻辑。下面分别分析。

### 关于desiredStateOfWorldPopulator
`desiredStateOfWorldPopulator`负责维护desired state world缓存的，下面简称dsw。对于每个active的pod，如果这个pod存在volume，dsw populator确保其在dsw缓存中；对于dsw缓存中的pod，如果被删除了，则从dsw缓存删除。dsw的Run方法就是调用`populatorLoop`方法。这个方法如下，省去了部分代码：
```golang
func (dswp *desiredStateOfWorldPopulator) populatorLoop() {
	dswp.findAndAddNewPods()

	// 这个方法需要调用container运行时接口，去查看一个pod的container是否被terminated，
	// 这个操作非常耗时，所以被限制为2秒钟执行一次。
	dswp.findAndRemoveDeletedPods()
}
```
#### dsw缓存中的数据结构
鉴于dsw populator主要是更新dsw缓存的，我们有必要先了解一下dsw缓存这个数据结构。
```go
type desiredStateOfWorld struct {

	// dsw是一个volume的集合，这些volume应该被attach到当前节点，并且被mount到使用它的pod中，
	// 这个map的key是volume的名字，value是关于这个volume的一些更详细的信息，即数据结构volumeToMount
	volumesToMount map[v1.UniqueVolumeName]volumeToMount
	
	// 插件管理器，用来根据volumeSpec来查找对应用途的插件 
	volumePluginMgr *volume.VolumePluginMgr
}
```
接着看`volumeToMount`，注意这个结构体的命名，volume-to-mount在英语中`to`表示要去做的事，当前可能没有做。
```go
type volumeToMount struct {
	volumeName v1.UniqueVolumeName

	// volumeToMount这个结构体含有一个字段podToMount，表示用了这个volume的pod的集合，
	// 在volume被attach到节点之后，这些pod应该立即mount这个volume，map的key是pod的名字，
	// value是关于这个pod的更详细的信息（数据结构是map，实际是一个set）。
	podsToMount map[types.UniquePodName]podToMount

	// 表示当前volume是否支持attachable
	pluginIsAttachable bool

	// 是否支持mountable
	pluginIsDeviceMountable bool

	// volumeGidValue contains the value of the GID annotation, if present.
	volumeGidValue string

	// reportedInUse表示是否成功添加到node status的VolumeInUse字段。
	reportedInUse bool
}
```
看`podToMount`数据结构，从定义上看，只是包含了pod的引用，以及一个volumeSpec。
```go
type podToMount struct {
	podName types.UniquePodName

	pod *v1.Pod
	volumeSpec *volume.Spec

	outerVolumeSpecName string
}
```
上面介绍了dsw缓存的数据结构，我们稍微看一下populatorLoop中的`findAndAddNewPods`是怎么实现的，后者主要调用了`processPodVolumes`，我们主要看这个方法吧：
```go
func (dswp *desiredStateOfWorldPopulator) processPodVolumes(
	pod *v1.Pod,
	mountedVolumesForPod map[volumetypes.UniquePodName]map[string]cache.MountedVolume,
	processedVolumesForFSResize sets.String) {

	allVolumesAdded := true
	mountsMap, devicesMap := dswp.makeVolumeMap(pod.Spec.Containers)

	// Process volume spec for each volume defined in pod
	for _, podVolume := range pod.Spec.Volumes {
		
		// 创建一个volume的深拷贝，如果是pvc还要取出对应的pv
		pvc, volumeSpec, volumeGidValue, err :=
			dswp.createVolumeSpec(podVolume, pod.Name, pod.Namespace, mountsMap, devicesMap)
		if err != nil {
			klog.Errorf(
				"Error processing volume %q for pod %q: %v",
				podVolume.Name,
				format.Pod(pod),
				err)
			dswp.desiredStateOfWorld.AddErrorToPod(uniquePodName, err.Error())
			allVolumesAdded = false
			continue
		}

		// 修改dsw缓存中的volumeToMount数据结构，主要是添加volumeToMount中的PodToMount项，
		// 如果volumeToMount不存在对应的volume，当然也要添加进去。
		_, err = dswp.desiredStateOfWorld.AddPodToVolume(
			uniquePodName, pod, volumeSpec, podVolume.Name, volumeGidValue)
		if err != nil {
			klog.Errorf(
				"Failed to add volume %s (specName: %s) for pod %q to desiredStateOfWorld: %v",
				podVolume.Name,
				volumeSpec.Name(),
				uniquePodName,
				err)
			dswp.desiredStateOfWorld.AddErrorToPod(uniquePodName, err.Error())
			allVolumesAdded = false
		}

	}

	// 如果所有的volume都正常处理了
	if allVolumesAdded {
		dswp.markPodProcessed(uniquePodName)
		// New pod has been synced. Re-mount all volumes that need it
		// (e.g. DownwardAPI)
		dswp.actualStateOfWorld.MarkRemountRequired(uniquePodName)
		// Remove any stored errors for the pod, everything went well in this processPodVolumes
		dswp.desiredStateOfWorld.PopPodErrors(uniquePodName)
	}

}
```
可以看到主线逻辑还算清晰。总结一下dsw populator：从podManager拿到所有的pod，如果这个pod之前没有添加到dsw的缓存中，则调用`processPodVolumes`更新缓存。

### 关于reconciler
`reconciler`是volume manager的主要循环控制逻辑，就是从当前状态到期望状态靠拢的逻辑，期望状态就是上面说的dsw，我们看下reconcile，主要循环逻辑为：
```go
func (rc *reconciler) reconciliationLoopFunc() func() {
	return func() {
		rc.reconcile()

		// 下面if语句的第一个case表示dsw的所有pod source是否ready，在ready之后，返回值都是true。
		// 第二个case表示是否执行过sync，如果执行过，这个条件就不成立，那么说来，这个sync方法只会执行一次
		if rc.populatorHasAddedPods() && !rc.StatesHasBeenSynced() {
			klog.Infof("Reconciler: start to sync state")
			rc.sync()
		}
	}
}
```
在看reconciler之前，还需要了解一下actual status of world这个数据结构，后面简称asw。

#### 关于actual status of world
这个是kubelet看到的关于volume当前状态，我们主要看这个缓存包括哪些数据结构，是如何构建以及更新的。这个缓存所在文件为`pkg/kubelet/volumemanager/cache/actual_state_of_world.go`，缓存包含的数据结构跟dsw非常像:
```go
type actualStateOfWorld struct {
	// 当前节点的名字，Attach/Detach的时候用
	nodeName types.NodeName

	// 已经被成功attach到该节点的volume的集合，如果volume对应的插件没有实现attach接口，
	// 会被默认添加到这个集合中
	attachedVolumes map[v1.UniqueVolumeName]attachedVolume

	volumePluginMgr *volume.VolumePluginMgr
}
```
下面看`attachedVolume`
```go
type attachedVolume struct {
	volumeName v1.UniqueVolumeName

	// pod的集合，当前volume被成功mount到pod后，pod会被加入到这个集合中。
	mountedPods map[volumetypes.UniquePodName]mountedPod

	// 被attach的volume的详细信息
	spec *volume.Spec

	// 插件名字
	pluginName string

	pluginIsAttachable bool

	// 是否被mount到global mount point，被detach之前需要从这个global umount
	globallyMounted bool

	// devicePath contains the path on the node where the volume is attached for
	// attachable volumes
	devicePath string

	// deviceMountPath contains the path on the node where the device should
	// be mounted after it is attached.
	deviceMountPath string
}
```
`mountedPod`表示pod的信息，含有一个volumeSpec，表示pod的这个volume已经成功mount了。代码就不贴了，对asw的更新主要在reconcile的逻辑中，在分析reconcile时，会着重看一下缓存是如何更新的。下面直接看reconciler吧。

#### volume manager的reconcile
`reconcile`是volume manager的核心，这个方法主要是三个for循环，结构如下，分开来分析，这些代码细节较多，只是进行概述，如果有必要，以后在详细查看某一点。另外这三个for循环顺序是固定的，调整哪一个都不行。
```go
func (rc *reconciler) reconcile() {
	// 第一个for循环，umount应该被umount的volume
	// 第二个for循环，对应该被attached/mounted的volume，执行attach和mount操作。
	// 第三个for循环，保证应该被detached/unmounted的volume被detached/unmounted了。
}
```
我们先看第一个，对应该执行umount的volume执行umount操作，这个简单一点，`GetMouontedVolumes`这个方法返回成功mount的volume，以及对应的pod，可以理解成是一个pair。
```go
// 从asw缓存，遍历所有成功mount的volume
for _, mountedVolume := range rc.actualStateOfWorld.GetMountedVolumes() {
	// PodExistsInVolume这个方法首先根据volume名字查找dsw的volumeToMount缓存，
	// 如果volumeToMount缓存存在，则根据pod名字
	// 查看PodToMount缓存，如果其中一个缓存没查到，则返回false。
	if !rc.desiredStateOfWorld.PodExistsInVolume(mountedVolume.PodName, mountedVolume.VolumeName) {
		// Volume被成功mount过，现在缓存中查不到了，开始umount。
		// 这个umount首先根据volumeSpec查找plugin，然后调用plugin的umounter的TearDown方法
		// 成功之后，调用asw的MarkVolumeAsUnmounted方法，将pod从asw的attachedVolumes缓存的mountedPods缓存删除。
		err := rc.operationExecutor.UnmountVolume(
			mountedVolume.MountedVolume, rc.actualStateOfWorld, rc.kubeletPodsDir)
	}
}
```
我们再看第二个，对volume执行attached/mounted操作，这个代码多了一些，总的来说，就是dws缓存的状态，来对volume进行attach和mount操作，同时
更新asw缓存。
```go
// 遍历dsw的VolumeToMount缓存，遍历VolumeToMount的PodToMount缓存，（没错，双重循环），返回每一个volume-pod pair。
for _, volumeToMount := range rc.desiredStateOfWorld.GetVolumesToMount() {
	// 查找asw的attachedVolume缓存
	volMounted, devicePath, err := rc.actualStateOfWorld.PodExistsInVolume(volumeToMount.PodName, volumeToMount.VolumeName)
	volumeToMount.DevicePath = devicePath
	// 在asw的attachedVolume缓存中没有发现volume
	if cache.IsVolumeNotAttachedError(err) {
		// 第一个case是指使用attach/detach controller来进行attach操作，kubelet禁止进行attach操作。
		// 第二个case是指volume插件没有attachable接口
		// 等待a/d controller进行attach
		if rc.controllerAttachDetachEnabled || !volumeToMount.PluginIsAttachable {
			// Volume is not attached (or doesn't implement attacher), kubelet attach is disabled, 
			// wait for controller to finish attaching volume.
			klog.V(5).Infof(volumeToMount.GenerateMsgDetailed("Starting operationExecutor.VerifyControllerAttachedVolume", ""))
			// 这里介绍一下VerifyControllerAttachedVolume这个函数，这个函数会更新asw的attachedVolume缓存
			// 如果volume没有实现attachable接口，asw直接更新缓存，否则使用kubeclient获取node，
			// 查看node的status的VolumesAttached字段，
			// 如果在这个字段中，发更新asw缓存。这个字段是由ad controller更新的，
			//  代码路径pkg\controller\volume\attachdetach\statusupdater\node_status_updater.go。
			err := rc.operationExecutor.VerifyControllerAttachedVolume(
				volumeToMount.VolumeToMount,
				rc.nodeName,
				rc.actualStateOfWorld)
			if err != nil &&
				!nestedpendingoperations.IsAlreadyExists(err) &&
				!exponentialbackoff.IsExponentialBackoff(err) {
				// Ignore nestedpendingoperations.IsAlreadyExists and 
				// exponentialbackoff.IsExponentialBackoff errors, they are expected.
				// Log all other errors.
				klog.Errorf(volumeToMount.GenerateErrorDetailed(fmt.Sprintf("operationExecutor.VerifyControllerAttachedVolume failed (controllerAttachDetachEnabled %v)", rc.controllerAttachDetachEnabled), err).Error())
			}
			if err == nil {
				klog.Infof(volumeToMount.GenerateMsgDetailed("operationExecutor.VerifyControllerAttachedVolume started", ""))
			}
		} else {
			// Volume is not attached to node, kubelet attach is enabled, volume implements an attacher,
			// so attach it
			volumeToAttach := operationexecutor.VolumeToAttach{
				VolumeName: volumeToMount.VolumeName,
				VolumeSpec: volumeToMount.VolumeSpec,
				NodeName:   rc.nodeName,
			}
			klog.V(5).Infof(volumeToAttach.GenerateMsgDetailed("Starting operationExecutor.AttachVolume", ""))
			err := rc.operationExecutor.AttachVolume(volumeToAttach, rc.actualStateOfWorld)
			if err != nil &&
				!nestedpendingoperations.IsAlreadyExists(err) &&
				!exponentialbackoff.IsExponentialBackoff(err) {
				// Ignore nestedpendingoperations.IsAlreadyExists and 
				// exponentialbackoff.IsExponentialBackoff errors, they are expected.
				// Log all other errors.
				klog.Errorf(volumeToMount.GenerateErrorDetailed(fmt.Sprintf("operationExecutor.AttachVolume failed (controllerAttachDetachEnabled %v)", rc.controllerAttachDetachEnabled), err).Error())
			}
			if err == nil {
				klog.Infof(volumeToMount.GenerateMsgDetailed("operationExecutor.AttachVolume started", ""))
			}
		}
	} else if !volMounted || cache.IsRemountRequiredError(err) {
		// 在这个if语句中，第一个case是说pod不在asw的attachedVolume中，或者不在mountedPod缓存中，事实上，只可能是后者
		// 第二个case是说这个volume可能需要remount，比如downward api
		// Volume is not mounted, or is already mounted, but requires remounting
		remountingLogStr := ""
		isRemount := cache.IsRemountRequiredError(err)
		if isRemount {
			remountingLogStr = "Volume is already mounted to pod, but remount was requested."
		}
		klog.V(4).Infof(volumeToMount.GenerateMsgDetailed("Starting operationExecutor.MountVolume", remountingLogStr))

		// 对volume进行mount操作，大致分三个步骤：
		// 1. 调用attach接口的waitForAttach方法等待attach
		// 2. 调用mount接口的MountDevice方法将设备挂载到global path
		// 3. 调用mount接口Setup方法将global path挂载的pod的单个volume目录下面。
		err := rc.operationExecutor.MountVolume(
			rc.waitForAttachTimeout,
			volumeToMount.VolumeToMount,
			rc.actualStateOfWorld,
			isRemount)
		if err != nil &&
			!nestedpendingoperations.IsAlreadyExists(err) &&
			!exponentialbackoff.IsExponentialBackoff(err) {
			// Ignore nestedpendingoperations.IsAlreadyExists and exponentialbackoff.IsExponentialBackoff errors, they are expected.
			// Log all other errors.
			klog.Errorf(volumeToMount.GenerateErrorDetailed(fmt.Sprintf("operationExecutor.MountVolume failed (controllerAttachDetachEnabled %v)", rc.controllerAttachDetachEnabled), err).Error())
		}
		if err == nil {
			if remountingLogStr == "" {
				klog.V(1).Infof(volumeToMount.GenerateMsgDetailed("operationExecutor.MountVolume started", remountingLogStr))
			} else {
				klog.V(5).Infof(volumeToMount.GenerateMsgDetailed("operationExecutor.MountVolume started", remountingLogStr))
			}
		}
	} else if cache.IsFSResizeRequiredError(err) &&
		utilfeature.DefaultFeatureGate.Enabled(features.ExpandInUsePersistentVolumes) {
		klog.V(4).Infof(volumeToMount.GenerateMsgDetailed("Starting operationExecutor.ExpandInUseVolume", ""))
		err := rc.operationExecutor.ExpandInUseVolume(
			volumeToMount.VolumeToMount,
			rc.actualStateOfWorld)
		if err != nil &&
			!nestedpendingoperations.IsAlreadyExists(err) &&
			!exponentialbackoff.IsExponentialBackoff(err) {
			// Ignore nestedpendingoperations.IsAlreadyExists and exponentialbackoff.IsExponentialBackoff errors, they are expected.
			// Log all other errors.
			klog.Errorf(volumeToMount.GenerateErrorDetailed("operationExecutor.ExpandInUseVolume failed", err).Error())
		}
		if err == nil {
			klog.V(4).Infof(volumeToMount.GenerateMsgDetailed("operationExecutor.ExpandInUseVolume started", ""))
		}
	}
}

```
下面看第三个for循环，保证应该被detached/unmounted的volume被detached/unmounted了，
```go
// 遍历asw的attachedvolume缓存，返回所有mountedpod为空的volume
for _, attachedVolume := range rc.actualStateOfWorld.GetUnmountedVolumes() {
	// Check IsOperationPending to avoid marking a volume as detached if it's in the process of mounting.(这个是上一个for循环做的事)
	if !rc.desiredStateOfWorld.VolumeExists(attachedVolume.VolumeName) &&
		!rc.operationExecutor.IsOperationPending(attachedVolume.VolumeName, nestedpendingoperations.EmptyUniquePodName) {
		// GloballyMounted这个字段是在上个for循环中，调用MountDevice成功之后更新的。
		if attachedVolume.GloballyMounted {
			// Volume is globally mounted to device, unmount it
			klog.V(5).Infof(attachedVolume.GenerateMsgDetailed("Starting operationExecutor.UnmountDevice", ""))
			// 从global mount path卸载
			err := rc.operationExecutor.UnmountDevice(
				attachedVolume.AttachedVolume, rc.actualStateOfWorld, rc.hostutil)
			if err != nil &&
				!nestedpendingoperations.IsAlreadyExists(err) &&
				!exponentialbackoff.IsExponentialBackoff(err) {
				// Ignore nestedpendingoperations.IsAlreadyExists and exponentialbackoff.IsExponentialBackoff errors, they are expected.
				// Log all other errors.
				klog.Errorf(attachedVolume.GenerateErrorDetailed(fmt.Sprintf("operationExecutor.UnmountDevice failed (controllerAttachDetachEnabled %v)", rc.controllerAttachDetachEnabled), err).Error())
			}
			if err == nil {
				klog.Infof(attachedVolume.GenerateMsgDetailed("operationExecutor.UnmountDevice started", ""))
			}
		} else {
			// Volume is attached to node, detach it
			// Kubelet not responsible for detaching or this volume has a non-attachable volume plugin.
			if rc.controllerAttachDetachEnabled || !attachedVolume.PluginIsAttachable {
				rc.actualStateOfWorld.MarkVolumeAsDetached(attachedVolume.VolumeName, attachedVolume.NodeName)
				klog.Infof(attachedVolume.GenerateMsgDetailed("Volume detached", fmt.Sprintf("DevicePath %q", attachedVolume.DevicePath)))
			} else {
				// Only detach if kubelet detach is enabled
				klog.V(5).Infof(attachedVolume.GenerateMsgDetailed("Starting operationExecutor.DetachVolume", ""))
				// 调用attachable接口的detach方法
				err := rc.operationExecutor.DetachVolume(
					attachedVolume.AttachedVolume, false /* verifySafeToDetach */, rc.actualStateOfWorld)
				if err != nil &&
					!nestedpendingoperations.IsAlreadyExists(err) &&
					!exponentialbackoff.IsExponentialBackoff(err) {
					// Ignore nestedpendingoperations.IsAlreadyExists && exponentialbackoff.IsExponentialBackoff errors, they are expected.
					// Log all other errors.
					klog.Errorf(attachedVolume.GenerateErrorDetailed(fmt.Sprintf("operationExecutor.DetachVolume failed (controllerAttachDetachEnabled %v)", rc.controllerAttachDetachEnabled), err).Error())
				}
				if err == nil {
					klog.Infof(attachedVolume.GenerateMsgDetailed("operationExecutor.DetachVolume started", ""))
				}
			}
		}
	}
}
}
```
