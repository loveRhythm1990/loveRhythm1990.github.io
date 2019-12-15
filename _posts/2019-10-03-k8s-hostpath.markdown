---
layout:     post
title:      "k8s hostpath实现"
subtitle:   " \"如何跟docker对接\""
date:       2019-10-03 09:22:00
author:     "wm"
header-img-credit: false
tags:
    - k8s
    - kubelet
    - volume
---

## 前言
k8s的`hostPath`可以将host上的一个文件或者目录挂载到容器中，具体是怎么实现的呢？或者说，是调用的docker的什么接口，这里稍微分析总结一下。

hostPath的官方文档为：[https://kubernetes.io/docs/concepts/storage/volumes/#hostpath](https://kubernetes.io/docs/concepts/storage/volumes/#hostpath)

[2019.12.15更新]
如果不用考虑的很多，是调用的cri接口，这部分内容包括两部分，一个是参数如何在k8s这边准备的，另一个是runc如何mount目录的，前者的话比较好处理，后者可能涉及mount namespace的一些东西，等了解了之后再补充。

## kubeGenericRuntimeManager
kubelet的syncPod方法是主要的同步pod的逻辑，其函数签名为`(kl *Kubelet) syncPod(o syncPodOptions) error `，所在文件为`pkg/kubelet/kubelet.go`，syncPod的方法注释描述了同步一个pod的流程，具体可以去看代码，这里不叙述。在syncPod方法中，我们关注两点：
1. 等待pod的volume都挂载，如果pod有volume还没有挂载，就返回`"timeout expired waiting for volumes to attach/mount for pod %q/%q. list of unattached/unmounted volumes=%v`错误，并等待下一次同步，一般来说这里的挂载是指，kubelet正确处理了mount请求，并更新了VolumeManager asw缓存。如果在asw缓存中没有找到所有要mount的volume，就认为还有volume没有挂载。另外，这里的volume也包括secret以及configmap等。
```go
// Wait for volumes to attach/mount
if err := kl.volumeManager.WaitForAttachAndMount(pod); err != nil {
	glog.Errorf("Unable to mount volumes for pod %q: %v; skipping pod", format.Pod(pod), err)
	return err
}
```
2. runtimeManager同步pod，具体是kubeGenericRuntimeManager结构体的syncPod方法。这个syncPod方法是为pod创建具体container的同步过程，创建container要调用runtime service的cri接口，另外还要准备一堆参数。我们的mount参数就是在这里准备的。我们可以看一下准备参数的过程。
```go
// Call the container runtime's SyncPod callback
result := kl.containerRuntime.SyncPod(pod, apiPodStatus, podStatus, pullSecrets, kl.backOff)
```
参数生成过程，mount的参数准备是在`(kl *Kubelet) GenerateRunContainerOptions(pod *v1.Pod, container *v1.Container, podIP string) (*kubecontainer.RunContainerOptions, func(), error)`方法，
这个方法首先从VolumeManager拿到所有已经挂载的Volume，这个是从asw缓存拿的。asw中对每个volume保存了一个`Mounter`，类型是`volume.Mounter`接口，这个接口在`pkg/volume/volume.go`中，通过这个接口我们可以知道这个volume在物理机上的挂载目录，具体是通过`GetPath`方法。
```go
volumes := kl.volumeManager.GetMountedVolumesForPod(podName)
```
然后调用makeMounts组装参数，在这个方法中是调用`hostPath, err := volume.GetPath(vol.Mounter)`Mounter的getPath获取volume在主机的挂载路径的。
```go
mounts, cleanupAction, err := makeMounts(pod, kl.getPodDir(pod.UID), container, hostname, hostDomainName, podIP, volumes, kl.mounter)
```

## VolumePlugin
VolumePlugin在文件`pkg/volume/plugins.go`，是自定义volume插件必须要实现的接口，接口的一个方法是`NewMounter`
```go
	// NewMounter creates a new volume.Mounter from an API specification.
	// Ownership of the spec pointer in *not* transferred.
	// - spec: The v1.Volume spec
	// - pod: The enclosing pod
	NewMounter(spec *Spec, podRef *v1.Pod, opts VolumeOptions) (Mounter, error)
```
返回值`Mounter`也是一个接口，其内嵌了`Volume`接口，后者定义了`GetPath`方法，返回这个volume在主机上的挂载点，kubeGenericRuntimeManager就是调用这个方法，返回volume在主机上的挂载点的，一般来说，这个目录是/var/lib/pods/podUUID/volumes/PluginName/VolumeName。