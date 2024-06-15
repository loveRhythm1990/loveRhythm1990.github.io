---
layout:     post
title:      "Kubelet 容器运行时/CRI/CNI 初始化"
date:       2020-06-21 14:10:00
author:     "weak old dog"
header-img-credit: false
tags:
    - K8s
---

kubelet的 [syncPod方法](https://loverhythm1990.github.io/2019/12/14/kubelet-brief/) 是同步Pod的主要方法，在这个方法的最后调用了`kl.containerRuntime.SyncPod`方法，其实现是`kubeGenericRuntimeManager.SyncPod`，其代码目录为：`pkg/kubelet/kuberuntime/kuberuntime_manager.go`

本文按照代码顺序，依次介绍 CRI（服务端/客户端）、kubeGenericRuntimeManager、CNI 初始化。其中 CNI 初始化相对复杂，这里不详细介绍，只是将整个过程串联起来。

### CRI 初始化
对于 kubelet 来说，是 CRI 接口调用的客户端，于是这里的初始化包括两部分：
* CRI grpc server 的初始化，即 dockershim 的初始化，dockershim 是一个 CRI server 的实现，作为 kubelet 以及 docker 中间的一个桥梁
* CRI grpc 客户端的初始化，这个是容器运行时无关的，kubelet 只管通过一个 unix 接口初始化一个 CRI grpc 就好了

初始化的代码在下面：
```go
// 这里的 containerRuntime 是 docker，不要纠结为什么是 docker，以及参数怎么初始化及传递的，
// 因为我们就是用的docker，没有用 containerd 或者其他，分析问题要抓住主要矛盾
switch containerRuntime {
case kubetypes.DockerContainerRuntime:
	// Create and start the CRI shim running as a grpc server.
	streamingConfig := getStreamingConfig(kubeCfg, kubeDeps, crOptions)
	
	// 这里有个 NewDockerService 方法非常重要， 这个方法返回的 DockerService 接口实现了 CRIService，即所有的 CRI 接口
	ds, err := dockershim.NewDockerService(kubeDeps.DockerClientConfig, crOptions.PodSandboxImage, streamingConfig,
		&pluginSettings, runtimeCgroups, kubeCfg.CgroupDriver, crOptions.DockershimRootDirectory,
		!crOptions.RedirectContainerStreaming, kubeCfg.CpuSetCpus, kubeCfg.CpuSetMems, kubeCfg.Swap)
	if err != nil {
		return nil, err
	}

	// 这里：
	// remoteRuntimeEndpoint: unix:///var/run/dockershim.sock
	// remoteImageEndpoint: 这个没指定，默认跟 remoteRuntimeEndpoint 一致
	// The unix socket for kubelet <-> dockershim communication.
	klog.V(5).Infof("RemoteRuntimeEndpoint: %q, RemoteImageEndpoint: %q",remoteRuntimeEndpoint,remoteImageEndpoint)
	
	
	// 启动 dockershim GRPC server
	klog.V(2).Infof("Starting the GRPC server for the docker CRI shim.")
	server := dockerremote.NewDockerServer(remoteRuntimeEndpoint, ds)
	if err := server.Start(); err != nil {
		return nil, err
	}
    // ... ...
case kubetypes.RemoteContainerRuntime:
	// No-op.
	break
default:
	return nil, fmt.Errorf("unsupported CRI runtime: %q", containerRuntime)
}
// CRI 客户端初始化：runtimeService 以及 imageService
runtimeService, imageService, err := getRuntimeAndImageServices(remoteRuntimeEndpoint, remoteImageEndpoint, kubeCfg.RuntimeRequestTimeout)
if err != nil {
	return nil, err
}
klet.runtimeService = runtimeService
```
dockershim 的初始化可以通过下图来表示，dockershim 的桥梁作用可见一斑。
![java-javascript](/img/in-post/all-in-one/2022-12-16-11-28-09.png){:height="70%" width="70%"}

### kubeGenericRuntimeManager 初始化
其初始化代码在 `kubelet.go` 中，在这个结构体的初始化中，一个主要的参数就是 `runtimeService`，这个参数就是 **CRI初始化** 中生成的 runtimeService，也就是一个 cri grpc 的客户端，
```go
	runtime, err := kuberuntime.NewKubeGenericRuntimeManager(
		kubecontainer.FilterEventRecorder(kubeDeps.Recorder),
		klet.livenessManager,
		seccompProfileRoot,
		containerRefManager,
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
		runtimeService,
		imageService,
		kubeDeps.ContainerManager.InternalContainerLifecycle(),
		legacyLogProvider,
		klet.runtimeClassManager,
		kubeCfg.EnableP2P,
	)
```
kubeGenericRuntimeManager 封装了很多跟容器运行时相关的逻辑，其中最重要的就是 SyncPod，后面会有介绍。

### RunPodSandbox 接口
`RunPodSandbox` 是 CRI 众多接口中的一个，在 docker 的实现中，这个接口的作用是起一个 pause 容器，并且配置一个独立的网络 namespace，我们通过这个接口，看一下 dockershim 是怎么起一个承上启下的作用的。在本文前面内容中，我们已经提到了，在 kubelet 同步 pod 的主要逻辑 `(kl *Kubelet) syncPod(o syncPodOptions) error`中，最终调用了 `containerRuntime.SyncPod` 也即 `kubeGenericRuntimeManager` 的 `SyncPod` 方法，那么容器的启动和销毁也是在这个方法中的，我们在这个方法中找下 `RunPodSandbox` 的调用。

**kubeGenericRuntimeManager** 这个方法的注释写的很清楚了，必要的时候，会在第 4 步创建 sandbox。
```go
// SyncPod syncs the running pod into the desired pod by executing following steps:
//
//  1. Compute sandbox and container changes.
//  2. Kill pod sandbox if necessary.
//  3. Kill any containers that should not be running.
//  4. Create sandbox if necessary.
//  5. Create ephemeral containers.
//  6. Create init containers.
//  7. Create normal containers.
func (m *kubeGenericRuntimeManager) SyncPod(pod *v1.Pod, podStatus *kubecontainer.PodStatus, pullSecrets []v1.Secret, backOff *flowcontrol.Backoff) (result kubecontainer.PodSyncResult) {
	// ... ...
	// Step 4: Create a sandbox for the pod if necessary.
	podSandboxID := podContainerChanges.SandboxID
	if podContainerChanges.CreateSandbox {
		podSandboxID, msg, err = m.createPodSandbox(pod, podContainerChanges.Attempt)
		// ... ...
	}
    // ... ...
}

// 调用 CRI 客户端，这个客户端会根据 dockershim 的 unix 地址向 dockershim grpc server 发送请求
// createPodSandbox creates a pod sandbox and returns (podSandBoxID, message, error).
func (m *kubeGenericRuntimeManager) createPodSandbox(pod *v1.Pod, attempt uint32) (string, string, error) {
    // ... ...
	podSandBoxID, err := m.runtimeService.RunPodSandbox(podSandboxConfig, runtimeHandler)
    // ... ...
	return podSandBoxID, "", nil
```

下面再来看 dockershim 中 RunPodSandbox 的实现，在 dockershim 中 `dockerService` 是主要的运行时实现者，我们只看下大概调用关系，不分析代码逻辑，RunPodSandbox 代码中也分了步骤：1.2.3.4，我们保留这部分注释。
```go
// RunPodSandbox creates and starts a pod-level sandbox. Runtimes should ensure
// the sandbox is in ready state.
// For docker, PodSandbox is implemented by a container holding the network
// namespace for the pod.
// Note: docker doesn't use LogDirectory (yet).
func (ds *dockerService) RunPodSandbox(ctx context.Context, r *runtimeapi.RunPodSandboxRequest) (*runtimeapi.RunPodSandboxResponse, error) {

	// Step 1: Pull the image for the sandbox.
	if err := ensureSandboxImageExists(ds.client, image); err != nil {
		return nil, err
	}

    // sandbox 也是一个容器，调用 docker 的 http client
	// Step 2: Create the sandbox container.
	createResp, err := ds.client.CreateContainer(*createConfig)
	if err != nil {
		createResp, err = recoverFromCreationConflictIfNeeded(ds.client, *createConfig, err)
	}

	// Step 3: Create Sandbox Checkpoint.
	if err = ds.checkpointManager.CreateCheckpoint(createResp.ID, constructPodSandboxCheckpoint(config)); err != nil {
		return nil, err
	}

	// Step 4: Start the sandbox container.
	// Assume kubelet's garbage collector would remove the sandbox later, if
	// startContainer failed.
	err = ds.client.StartContainer(createResp.ID)
	if err != nil {
		return nil, fmt.Errorf("failed to start sandbox container for pod %q: %v", config.Metadata.Name, err)
	}

    // 第五步这一步非常重要，这一步是配置容器网络，所以CNI相关的东西，应该从这里入口
	// Step 5: Setup networking for the sandbox.
	// All pod networking is setup by a CNI plugin discovered at startup time.
	// This plugin assigns the pod ip, sets up routes inside the sandbox,
	// creates interfaces etc. In theory, its jurisdiction ends with pod
	// sandbox networking, but it might insert iptables rules or open ports
	// on the host as well, to satisfy parts of the pod spec that aren't
	// recognized by the CNI standard yet.
	err = ds.network.SetUpPod(config.GetMetadata().Namespace, config.GetMetadata().Name, cID, config.Annotations, networkOptions)
	if err != nil {
        // ... ... 
		return resp, utilerrors.NewAggregate(errList)
	}
	return resp, nil
}
```
在上面代码中，`ds.network.SetUpPod` 是配置容器网络配置的入口，如果想定制化容器网络，大概要从这个地方入手了。

### CNI 插件初始化
CNI 的初始化是在上面 CRI 初始化中的 `dockershim.NewDockerService` 中实现的，也就说在 Kubelet 初始化时，将 CNI 进行了初始化。

下面是 dockershim 初始化的主要代码，另外这个地方只是初始化，还没有启动 grpc server。对于 CNI 的初始化，就是读取配置文件目录 `/etc/cni/net.d` 的配置文件并初始化。
```go
// NewDockerService creates a new `DockerService` struct.
// NOTE: Anything passed to DockerService should be eventually handled in another way when we switch to running the shim as a different process.
func NewDockerService(config *ClientConfig, podSandboxImage string,
	streamingConfig *streaming.Config, pluginSettings *NetworkPluginSettings,
	cgroupsName, kubeCgroupDriver, dockershimRootDir string, startLocalStreamingServer bool,
	defaultCpusetCpus, defaultCpusetMems, defaultSwap string) (DockerService, error) {

	ds := &dockerService{
		client:          c,
		os:              kubecontainer.RealOS{},
		podSandboxImage: podSandboxImage,
		streamingRuntime: &streamingRuntime{
			client:      client,
			execHandler: &NativeExecHandler{},
		},
		containerManager:          cm.NewContainerManager(cgroupsName, client),
		checkpointManager:         checkpointManager,
		startLocalStreamingServer: startLocalStreamingServer,
		networkReady:              make(map[string]bool),
		containerCleanupInfos:     make(map[string]*containerCleanupInfo),
	}

	// 探测 cni 插件，并进行初始化
	cniPlugins := cni.ProbeNetworkPlugins(pluginSettings.PluginConfDir, pluginSettings.PluginCacheDir, pluginSettings.PluginBinDirs)
	cniPlugins = append(cniPlugins, kubenet.NewPlugin(pluginSettings.PluginBinDirs, pluginSettings.PluginCacheDir))
	netHost := &dockerNetworkHost{
		&namespaceGetter{ds},
		&portMappingGetter{ds},
		&labelAnnotationGetter{ds},
	}
	plug, err := network.InitNetworkPlugin(cniPlugins, pluginSettings.PluginName, netHost, pluginSettings.HairpinMode, pluginSettings.NonMasqueradeCIDR, pluginSettings.MTU)
	if err != nil {
		return nil, fmt.Errorf("didn't find compatible CNI plugin with given settings %+v: %v", pluginSettings, err)
	}
	ds.network = network.NewPluginManager(plug)
	klog.Infof("Docker cri networking managed by %v", plug.Name())

	// ... ...
	return ds, nil
}
```

#### 参考
[如何在Kubernetes中实现容器原地升级](https://cloud.tencent.com/developer/article/1413743)

[揭秘：如何为 Kubernetes 实现原地升级？](https://mp.weixin.qq.com/s/CNLf8MHYGs_xeD4PxChR4A)