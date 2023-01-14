---
layout:     post
title:      "K8s 存储的一些概念"
date:       2020-03-30 10:10:00
author:     "weak old dog"
header-img-credit: false
tags:
    - K8s
    - 存储
---

本文主要参考《深入剖析Kubernetes》张磊，极客时间，侵权立删，建议购买正版

#### 主机目录作为本地存储的不足
hostpath或者`local Persistent Volume`作为本地存储时，会将本地的一个目录挂载到容器里面，对于此有下面结论：

不应该把宿主机的目录作为PV使用，因为：
* 本地目录的存储行为不可控，它所在的磁盘空间随时可能被应用写满（这种情况下，宿主机与容器是共享磁盘的，容器可以写挂载的目录，宿主机也可以写磁盘），设置造成整个宿主机宕机
* 不同的本地目录之间也缺乏最基础的IO隔离机制。

基于上述原因：一个local volume对应的存储介质，一定是一块额外挂载在宿主机的磁盘或者块设备，（额外的意思是，不跟宿主机共享硬盘）。这个原则我们可以称为“一个pv一块盘”

#### local volume的延时绑定
pvc与pv的绑定就是设置pvc的VolumeName字段，(其实还要设置pv的claimReference)，在官方提供的解决方案中，应该没有实现动态provision，在这种情况下，需要事先在各个node上创建一批pv，（这个听上去有点奇怪，什么叫`在节点上创建pv呢`，其实是通过pv的nodeAffinity实现的，只有local volume才有nodeAffinity字段），比如下面的pv，其实是在node-1上，其它节点都是不存在的:
```yaml
apiVersion: v1
kind: PersistentVolume
metadata:
  name: example-pv
spec:
  capacity:
    storage: 5Gi
  volumeMode: Filesystem
  accessModes:
  - ReadWriteOnce
  persistentVolumeReclaimPolicy: Delete
  storageClassName: local-storage
  local:
    path: /mnt/disks/vol1
  nodeAffinity:
    required:
      nodeSelectorTerms:
      - matchExpressions:
        - key: kubernetes.io/hostname
          operator: In
          values:
          - node-1
```
那就导致一个问题，当sts中的pod声明一个pvc时，我不能立即将这个pvc跟local volume pv绑定，因为我一旦绑定了，local volume pv的node就确定了，然而，pod此时还没有调度呢。所以就需要`延迟绑定：在pod调度的时候再将pvc与pv绑定`，这个时候需要node的列表，也需要知道node上所有的local volume pv列表。

上面我们说`需要事先在每个节点上创建pv`，其实也不一定要创建pv这个资源，k8s给我们提供了一个`static provisioner`，参考下面的连接，这个是个static的，它的工作原理是这样的：每个节点跑一个DaemonSet，这个DaemonSet定期扫描/mnt/disks目录下（这个目录应该是可以配置的），对于这个目录下面的每一个挂载目录（每个目录还是一定要是挂载点）都创建这个pv出来，当然这个pv也跟当前节点绑定了，

#### pvc与pv的关系
实际上是一种解耦关系，pvc是一种声明，pv是具体实现，生成pv是存储开发者的事情。

#### “持久化”宿主机目录，Attach与Mount
容器的volume，就是将宿主机上的一个目录，跟一个容器里的目录绑定挂载（linux中的bind mount）在了一起。

**Attach**

因为一直没有使用过网络存储，对Attach的实现一直有点模糊，这个可能跟具体的网络存储有关了，以Google Cloud的Persistent Disk（GCE提供远程磁盘服务），Attach的操作是:调用Google cloud的API，将它所提供的Persistent Disk挂载到Pod所在的宿主机上。相当于执行：
```s
$ gcloud compute instances attach-disk <虚拟机名字> --disk <远程磁盘名字>
```
为虚拟机挂载远程磁盘的操作，在Kubernetes中称为Attach。

**Mount**
格式化这个磁盘设备，然后将它挂载到宿主机指定的挂载点上，比如`/var/lib/kubelet/pods/<Pod的ID>/volumes/kubernetes.io~<Volume类型>/<Volume名字>`。

Kubernetes如何定义和区分这两个阶段？在具体的 Volume 插件的实现接口上，Kubernetes分别给这两个阶段提供了两种不同的参数列表：
* 对于“第一阶段”（Attach），Kubernetes 提供的可用参数是nodeName，即宿主机的名字。
* 而对于“第二阶段”（Mount），Kubernetes 提供的可用参数是dir，即Volume的宿主机目录。

#### csi插件相关
我们知道一个volume在K8s里的声明周期有provision/delete，attach/detach，mount/umount等。那csi就是把这些代码给抽象出来了，做成了单独的插件，之前都是in tree的，现在不需要嵌入到k8s代码里面了，成了out tree了。
在k8s的[CSI文档](https://kubernetes-csi.github.io/docs/sidecar-containers.html)中，列出了所有的sidecar containers列表：
* external-provisioner
* external-attacher
* external-snapshotter
* external-resizer
* node-driver-registrar
* cluster-driver-registrar (deprecated)
* livenessprobe

上面这些sidecar container是由kubernetes存储小组来开发和维护的。这些container严格来说是可选的，但是非常推荐使用。

CSI组件众多，先看一下图片有个大体印象。

![java-javascript](/img/in-post/storage-csi/Screenshot_1.png)

首先看三个独立的外部组件（External Components），这个在上面的表中也列出来了，是一些sidecar container，即：
* Driver Registrar, 负责将插件注册到kubelet里面，在具体实现上，Driver Registrar需要请求CSI插件的Identity服务来获取插件信息。这个应该是使用pluginmanager来注册csi插件的，思路就是扫描/var/lib/kubelet/\<plugin name\>/csi.sock
* External Provisioner组件，负责的正是Provision阶段，在具体实现上，External Provisioner监听（Watch）了APIServer里面的PVC对象。当一个PVC被创建出来的时候，它就会调用CSI Controller的CreateVolume方法，为你创建对应的PV。
* 最后一个External Attacher组件，负责的是Attach，在具体实现上，它监听了APIServer里 VolumeAttachment对象的变化。VolumeAttachment对象是Kubernetes确认一个Volume可以进入“Attach 阶段”的重要标志，一旦出现了VolumeAttachment对象，External Attacher就会调用CSI Controller服务的ControllerPublish方法，完成它所对应的Volume的 Attach 阶段。

下面看CSI插件里的三个服务：CSI Identity、CSI Controller和CSI Node。
首先看CSI Identity，这个主要做两件事，第一个是获取当前插件的名字，第二个是获取当前插件支持的Capability，也就是支持的功能，其proto定义如下：
```protobuf
service Identity {
  // 返回插件的版本以及名字信息
  rpc GetPluginInfo(GetPluginInfoRequest)
    returns (GetPluginInfoResponse) {}
  // reports whether the plugin has the ability of serving the Controller interface
  rpc GetPluginCapabilities(GetPluginCapabilitiesRequest)
    returns (GetPluginCapabilitiesResponse) {}
  // called by the CO just to check whether the plugin is running or not
  rpc Probe (ProbeRequest)
    returns (ProbeResponse) {}
}
```
而 CSI Controller 服务，定义的则是对 CSI Volume（对应 Kubernetes 里的 PV）的管理接口，比如：创建和删除 CSI Volume、对 CSI Volume 进行 Attach/Dettach（在 CSI 里，这个操作被叫作 Publish/Unpublish），以及对 CSI Volume 进行 Snapshot 等，它们的接口定义如下所示
```go

service Controller {
  // provisions a volume
  rpc CreateVolume (CreateVolumeRequest)
    returns (CreateVolumeResponse) {}
    
  // deletes a previously provisioned volume
  rpc DeleteVolume (DeleteVolumeRequest)
    returns (DeleteVolumeResponse) {}
    
  // make a volume available on some required node
  rpc ControllerPublishVolume (ControllerPublishVolumeRequest)
    returns (ControllerPublishVolumeResponse) {}
    
  // make a volume un-available on some required node
  rpc ControllerUnpublishVolume (ControllerUnpublishVolumeRequest)
    returns (ControllerUnpublishVolumeResponse) {}
    
  ...
  
  // make a snapshot
  rpc CreateSnapshot (CreateSnapshotRequest)
    returns (CreateSnapshotResponse) {}
    
  // Delete a given snapshot
  rpc DeleteSnapshot (DeleteSnapshotRequest)
    returns (DeleteSnapshotResponse) {}
    
  ...
}
```
CSI controller里面的服务一般是由CSI的那些sidecar container来调用的。并且controller里面的服务并不依赖当前所在的宿主机。

而 CSI Volume 需要在宿主机上执行的操作，都定义在了 CSI Node 服务里面，如下所示：
```go

service Node {
  // temporarily mount the volume to a staging path
  rpc NodeStageVolume (NodeStageVolumeRequest)
    returns (NodeStageVolumeResponse) {}
    
  // unmount the volume from staging path
  rpc NodeUnstageVolume (NodeUnstageVolumeRequest)
    returns (NodeUnstageVolumeResponse) {}
    
  // mount the volume from staging to target path
  rpc NodePublishVolume (NodePublishVolumeRequest)
    returns (NodePublishVolumeResponse) {}
    
  // unmount the volume from staging path
  rpc NodeUnpublishVolume (NodeUnpublishVolumeRequest)
    returns (NodeUnpublishVolumeResponse) {}
    
  // stats for the volume
  rpc NodeGetVolumeStats (NodeGetVolumeStatsRequest)
    returns (NodeGetVolumeStatsResponse) {}
    
  ...
  
  // Similar to NodeGetId
  rpc NodeGetInfo (NodeGetInfoRequest)
    returns (NodeGetInfoResponse) {}
}
```

#### 参考

[CSI官方文档](https://kubernetes-csi.github.io/docs/introduction.html)

[Cgroup - Linux 的 IO 资源隔离](https://www.v2ex.com/t/251497)

[k8s提供的local volume的static provisioner](https://github.com/kubernetes-sigs/sig-storage-local-static-provisioner)