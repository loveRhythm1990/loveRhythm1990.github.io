---
layout:     post
title:      "kubelet runtimeservice初始化"
date:       2020-06-21 14:10:00
author:     "weak old dog"
header-img-credit: false
tags:
    - k8s
    - kubelet
---

kubelet的[syncPod方法](https://loverhythm1990.github.io/2019/12/14/kubelet-brief/)是同步Pod的主要方法，在这个方法的最后调用了`kl.containerRuntime.SyncPod`方法，交由运行时来同步Pod，Kubelet的`containerRuntime`是`kubeGenericRuntimeManager`，其代码目录为：

`pkg/kubelet/kuberuntime/kuberuntime_manager.go`

在`containerRuntime.SyncPod`方法中，最终调用了`containerRuntime.startContainer`方法来启动容器，后者的任务就是：
* pull the image
* create the container
* start the container
* run the post start lifecycle hooks (if applicable)

其中`create the container`调用的是`containerRuntime.runtimeService.CreateContainer`方法。这里的`runtimeService`定义是`CRI`接口。即这个`runtimeService`的实现需要实现CRI中过于运行时的接口。重点看一下`kubeGenericRuntimeManager`的这个`runtimeService`是怎么初始化的。

初始化`kubeGenericRuntimeManager`调用的是`NewKubeGenericRuntimeManager`函数，这个函数需要一个runtimeService的参数，这个参数就是CRI的接口，然后在`NewKubeGenericRuntimeManager`内部，使用`InstrumentedRuntimeService`对参数传进来的runtimeService进行了封装。
```go
		runtimeService:      newInstrumentedRuntimeService(runtimeService),
```

从`NewKubeGenericRuntimeManager`函数的调用开始向上推，看参数runtimeService的初始化过程。代码在`kubelet.go`
```go
	runtime, err := kuberuntime.NewKubeGenericRuntimeManager(
		kubecontainer.FilterEventRecorder(kubeDeps.Recorder),
		klet.livenessManager,
		klet.startupManager,
		seccompProfileRoot,
		machineInfo,
		klet,
		kubeDeps.OSInterface,
		klet,
		httpClient,
		imageBackOff,
		kubeCfg.SerializeImagePulls,
		float32(kubeCfg.RegistryPullQPS),
		int(kubeCfg.RegistryBurst),
		kubeCfg.CPUCFSQuota,
		kubeCfg.CPUCFSQuotaPeriod,
		kubeDeps.RemoteRuntimeService,
		kubeDeps.RemoteImageService,
		kubeDeps.ContainerManager.InternalContainerLifecycle(),
		kubeDeps.dockerLegacyService,
		klet.runtimeClassManager,
	)
```
看`kubeDeps.RemoteRuntimeService`的初始化，代码`kubelet.go`，下面是`PreInitRuntimeService`函数的代码片段。我们主要看下`remoteRuntimeEndpoint`初始化。
```go
	switch containerRuntime {
    case kubetypes.DockerContainerRuntime:
        // 启动docker shim grpc server
		if err := runDockershim(
			kubeCfg,
			kubeDeps,
			crOptions,
			runtimeCgroups,
			remoteRuntimeEndpoint,
			remoteImageEndpoint,
			nonMasqueradeCIDR,
		); err != nil {
			return err
		}
	case kubetypes.RemoteContainerRuntime:
		// No-op.
		break
	default:
		return fmt.Errorf("unsupported CRI runtime: %q", containerRuntime)
	}

    var err error
    // 初始化docker shim grpc客户端
	if kubeDeps.RemoteRuntimeService, err = remote.NewRemoteRuntimeService(remoteRuntimeEndpoint, kubeCfg.RuntimeRequestTimeout.Duration); err != nil {
		return err
	}
	if kubeDeps.RemoteImageService, err = remote.NewRemoteImageService(remoteImageEndpoint, kubeCfg.RuntimeRequestTimeout.Duration); err != nil {
		return err
	}
```
这个`NewRemoteRuntimeService`就是初始化了一个CRI的运行时接口，使用CRI gRPC初始化了一个Client。
```go
// NewRemoteRuntimeService creates a new internalapi.RuntimeService.
func NewRemoteRuntimeService(endpoint string, connectionTimeout time.Duration) (internalapi.RuntimeService, error) {
	klog.V(3).Infof("Connecting to runtime service %s", endpoint)
	addr, dialer, err := util.GetAddressAndDialer(endpoint)
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(context.Background(), connectionTimeout)
	defer cancel()

	conn, err := grpc.DialContext(ctx, addr, grpc.WithInsecure(), grpc.WithContextDialer(dialer), grpc.WithDefaultCallOptions(grpc.MaxCallRecvMsgSize(maxMsgSize)))
	if err != nil {
		klog.Errorf("Connect remote runtime %s failed: %v", addr, err)
		return nil, err
	}

	return &RemoteRuntimeService{
        timeout:       connectionTimeout,
        // 初始化一个gRPC的client
		runtimeClient: runtimeapi.NewRuntimeServiceClient(conn),
		logReduction:  logreduction.NewLogReduction(identicalErrorDelay),
	}, nil
}
```

`PreInitRuntimeService`是在kubelet的run方法里调用的。看下这个Endpoint是什么，
```go
	err = kubelet.PreInitRuntimeService(&s.KubeletConfiguration,
		kubeDeps, &s.ContainerRuntimeOptions,
		s.ContainerRuntime,
		s.RuntimeCgroups,
		s.RemoteRuntimeEndpoint,
		s.RemoteImageEndpoint,
		s.NonMasqueradeCIDR)
	if err != nil {
		return err
	}
```
Endpoint初始化代码如下，看来就是`dockershim`的sock地址了。也就是说`dockershim`是这里的gRPC服务的服务端。
```go
// NewKubeletFlags will create a new KubeletFlags with default values
func NewKubeletFlags() *KubeletFlags {
	remoteRuntimeEndpoint := ""
	if runtime.GOOS == "linux" {
		remoteRuntimeEndpoint = "unix:///var/run/dockershim.sock"
	} else if runtime.GOOS == "windows" {
		remoteRuntimeEndpoint = "npipe:////./pipe/dockershim"
	}

	return &KubeletFlags{
		ContainerRuntimeOptions:             *NewContainerRuntimeOptions(),
		CertDirectory:                       "/var/lib/kubelet/pki",
		RootDirectory:                       defaultRootDir,
		MasterServiceNamespace:              metav1.NamespaceDefault,
		MaxContainerCount:                   -1,
		MaxPerPodContainerCount:             1,
		MinimumGCAge:                        metav1.Duration{Duration: 0},
		NonMasqueradeCIDR:                   "10.0.0.0/8",
		RegisterSchedulable:                 true,
		ExperimentalKernelMemcgNotification: false,
		RemoteRuntimeEndpoint:               remoteRuntimeEndpoint,
		NodeLabels:                          make(map[string]string),
		RegisterNode:                        true,
		SeccompProfileRoot:                  filepath.Join(defaultRootDir, "seccomp"),
		// prior to the introduction of this flag, there was a hardcoded cap of 50 images
		EnableCAdvisorJSONEndpoints: false,
	}
}
```
综上所述，`containerRuntime.runtimeService.CreateContainer`这个方法其实就是调用了gRPC客户端的`CreateContainer`方法，向GRPC服务端`docker shim`发送了一个请求。而这个客户端的最终实现就是`RemoteRuntimeService`，我们看下其`CreateContainer`具体做了一些什么：
```go
// CreateContainer creates a new container in the specified PodSandbox.
func (r *RemoteRuntimeService) CreateContainer(podSandBoxID string, config *runtimeapi.ContainerConfig, sandboxConfig *runtimeapi.PodSandboxConfig) (string, error) {
	klog.V(10).Infof("[RemoteRuntimeService] CreateContainer (podSandBoxID=%v, timeout=%v)", podSandBoxID, r.timeout)
	ctx, cancel := getContextWithTimeout(r.timeout)
	defer cancel()

	resp, err := r.runtimeClient.CreateContainer(ctx, &runtimeapi.CreateContainerRequest{
		PodSandboxId:  podSandBoxID,
		Config:        config,
		SandboxConfig: sandboxConfig,
	})
	if err != nil {
		klog.Errorf("CreateContainer in sandbox %q from runtime service failed: %v", podSandBoxID, err)
		return "", err
	}

	klog.V(10).Infof("[RemoteRuntimeService] CreateContainer (podSandBoxID=%v, ContainerId=%v)", podSandBoxID, resp.ContainerId)
	if resp.ContainerId == "" {
		errorMessage := fmt.Sprintf("ContainerId is not set for container %q", config.GetMetadata())
		klog.Errorf("CreateContainer failed: %s", errorMessage)
		return "", errors.New(errorMessage)
	}

	return resp.ContainerId, nil
}
```
如上代码所示，就是调用了gRPC客户端发送了一个请求。

接下来还有一个问题，docker shim server什么时候启动的。是`remoteRuntimeEndpoint`方法的`runDockershim`，这个方法一方面启动了一个docker shim server，另一方面启动了docker shim 客户端。


#### 参考
[如何在Kubernetes中实现容器原地升级](https://cloud.tencent.com/developer/article/1413743)

[揭秘：如何为 Kubernetes 实现原地升级？](https://mp.weixin.qq.com/s/CNLf8MHYGs_xeD4PxChR4A)