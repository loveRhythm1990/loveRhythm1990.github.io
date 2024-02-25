---
layout:     post
title:      "理解 K8s CSI 存储插件机制"
date:       2020-06-06 10:10:00
author:     "weak old dog"
header-img-credit: false
tags:
    - K8s
---

**目录**
- [CSI Spec中的RPC接口](#csi-spec中的rpc接口)
  - [Identity Service](#identity-service)
  - [Controller Service](#controller-service)
  - [Node Service](#node-service)
- [In Tree CSI Plugin](#in-tree-csi-plugin)
- [关于通信方式](#关于通信方式)
  - [推荐的部署以及开发方式](#推荐的部署以及开发方式)
- [开发CSI驱动](#开发csi驱动)
  - [Kubernetes CSI Sidecar 容器](#kubernetes-csi-sidecar-容器)
- [在Kubernetes集群中部署CSI驱动](#在kubernetes集群中部署csi驱动)
  - [Controller组件](#controller组件)
  - [Node plugin](#node-plugin)


官方文档地址：[https://kubernetes-csi.github.io/docs/](https://kubernetes-csi.github.io/docs/)。

设计文档：[Container Storage Interface (CSI)](https://github.com/container-storage-interface/spec/blob/master/spec.md)

## CSI Spec中的RPC接口
CSI接口分三部分`Identity Service`、`Controller Service`、`Node Service`。其中
* Identidy Service: Node组件以及Controller组件都需要实现的接口。
* Controller Service: Controller组件需要实现的接口。
* Node Service: Node组件需要实现的接口。

在上面三类接口中，只有`Identiry Service`接口以及`Node Service`中的部分接口是required。`Controller Service`不是必须的。对这三类接口具体看下：

### Identity Service
其proto定义如下，重点关注下`GetPluginCapabilities`接口，该接口说明插件提供的能力，一般有：
* PluginCapability_Service_CONTROLLER_SERVICE: 表示实现了`Controller Service`的部分或全部接口。
* PluginCapability_Service_VOLUME_ACCESSIBILITY_CONSTRAINTS：表示volume对节点拓扑有要求，如果提供了此种能力，csi external provisioner在provision PVC的时候，还需要检查节点的拓扑是否符合PVC的需求。
* PluginCapability_VolumeExpansion_ONLINE：磁盘在线扩展。
* PluginCapability_VolumeExpansion_OFFLINE：磁盘离线扩展。

```protobuf
service Identity {
  // 获取插件名字，版本
  rpc GetPluginInfo(GetPluginInfoRequest)
    returns (GetPluginInfoResponse) {}
  // 获取CSI插件提供的能力
  rpc GetPluginCapabilities(GetPluginCapabilitiesRequest)
    returns (GetPluginCapabilitiesResponse) {}
  // 用于liveness probe
  rpc Probe (ProbeRequest)
    returns (ProbeResponse) {}
}
```

### Controller Service
`Controller Service`接口众多，我们只分析几个重要的。另外Controller Service的接口也都是可选的。

* CreateVolume/DeleteVolume: 这两个是最重要的两个接口，从字面看也容易理解，主要是负责具体后端存储介质的创建以及回收，这个过程对应的是provision过程，也就是provision一个具体的底层设备。
* ControllerPublishVolume/ControllerUnpublishVolume: 这两个就一时摸不着头脑了，这个其实是对应in-tree plugin的attach接口。简述一下工作流程：K8s的Attach-detach controller在监听到PVC调度到某个节点之后（这个过程在provision之后），需要调用这个PVC对应的存储插件的Attach接口（如果这个插件实现了Attach接口），将存储设备Attach到具体某个节点上，以网盘为例，这个过程就是在节点上挂一块网盘。AD Controller调用Attach接口，其实调用的是Intree CSI Plugin的Attach方法，这个方法会创建一个`VolumeAttachment` CR，这个CR被CSI `CSI external attacher`这个sidecar容器监听到之后，就会调用`ControllerPublishVolume`方法，这个方法执行具体的attach逻辑。detach过程与之类似，还是有点绕。

其他接口都比较容易理解，这里不再叙述。

```protobuf
service Controller {
  rpc CreateVolume (CreateVolumeRequest)
    returns (CreateVolumeResponse) {}
  rpc DeleteVolume (DeleteVolumeRequest)
    returns (DeleteVolumeResponse) {}
  rpc ControllerPublishVolume (ControllerPublishVolumeRequest)
    returns (ControllerPublishVolumeResponse) {}
  rpc ControllerUnpublishVolume (ControllerUnpublishVolumeRequest)
    returns (ControllerUnpublishVolumeResponse) {}
  rpc ValidateVolumeCapabilities (ValidateVolumeCapabilitiesRequest)
    returns (ValidateVolumeCapabilitiesResponse) {}
  rpc ListVolumes (ListVolumesRequest)
    returns (ListVolumesResponse) {}
  rpc GetCapacity (GetCapacityRequest)
    returns (GetCapacityResponse) {}
  rpc ControllerGetCapabilities (ControllerGetCapabilitiesRequest)
    returns (ControllerGetCapabilitiesResponse) {}
  rpc CreateSnapshot (CreateSnapshotRequest)
    returns (CreateSnapshotResponse) {}
  rpc DeleteSnapshot (DeleteSnapshotRequest)
    returns (DeleteSnapshotResponse) {}
  rpc ListSnapshots (ListSnapshotsRequest)
    returns (ListSnapshotsResponse) {}
  rpc ControllerExpandVolume (ControllerExpandVolumeRequest)
    returns (ControllerExpandVolumeResponse) {}
  rpc ControllerGetVolume (ControllerGetVolumeRequest)
    returns (ControllerGetVolumeResponse) {
        option (alpha_method) = true;
    }
}
```

### Node Service
`Node Service`包含必须实现的接口，其实也就是`NodePublishVolume`以及`NodeUnpublishVolume`，
* NodeStageVolume/NodeUnstageVolume：这个接口是将存储设备挂载到global mountpath的，global mountpath的路径我记得是`/var/lib/kubelet/plugins/volume`，我记得是这个，用到的时候再去翻一下。这个是可选的，global mountpath一般是一个volume供多个pod消费的时候采用，这个路径跟具体某个pod是没有关系的。一般情况下，我们直接将磁盘的路径挂载到`/var/lib/kubelet/pods/`路径下，这个是跟global path相对的，跟具体某个pod相关的挂载路径。
* NodePublishVolume/NodeUnpublishVolume：这个是将global path挂载到pod目录下面（这样的话得使用bind mount），或者直接将磁盘路径挂载到pod目录下面，这个是必须要实现的。
* NodeGetCapabilities：这个也是必须要实现的接口，有三个可配置项，`NodeServiceCapability_RPC_STAGE_UNSTAGE_VOLUME`：是否实现`NodeStageVolume`接口，`NodeServiceCapability_RPC_GET_VOLUME_STATS`是否实现`NodeGetVolumeStats`接口，`NodeServiceCapability_RPC_EXPAND_VOLUME`是否支持底层存储设备扩展。

```protobuf
service Node {
  rpc NodeStageVolume (NodeStageVolumeRequest)
    returns (NodeStageVolumeResponse) {}
  rpc NodeUnstageVolume (NodeUnstageVolumeRequest)
    returns (NodeUnstageVolumeResponse) {}
  rpc NodePublishVolume (NodePublishVolumeRequest)
    returns (NodePublishVolumeResponse) {}
  rpc NodeUnpublishVolume (NodeUnpublishVolumeRequest)
    returns (NodeUnpublishVolumeResponse) {}
  rpc NodeGetVolumeStats (NodeGetVolumeStatsRequest)
    returns (NodeGetVolumeStatsResponse) {}
  rpc NodeExpandVolume(NodeExpandVolumeRequest)
    returns (NodeExpandVolumeResponse) {}
  rpc NodeGetCapabilities (NodeGetCapabilitiesRequest)
    returns (NodeGetCapabilitiesResponse) {}
  rpc NodeGetInfo (NodeGetInfoRequest)
    returns (NodeGetInfoResponse) {}
}
```

## In Tree CSI Plugin
这里稍微介绍一下In Tree CSI Plugin，其实controller manager里面的Attach-detach controller以及Kubelet只与这个In-tree的Plugin交互，这个Intree Plugin再通过创建CR资源`VolumeAttachement`与CSI controller交互，或者通过UDS与CSI Node组件交互。

其实现的接口跟其他Intree plugin实现的接口基本一致，比如：Attach:将磁盘添加到节点；MountDevice:将磁盘挂载到Global mountpath；Setup：将磁盘从global path挂载到具体某个pod路径下，或者直接挂载磁盘到pod目录下。这个插件没有实现Provisioner接口，这个是通过CSI external Provisioner来实现的，其通过调用CSI controller的CreateVolume接口实现存储设备创建。

## 关于通信方式
K8s与csi组件有两种通信方式，分下面两部分
* Kubelet与CSI驱动通信
    - Kubelet直接向CSI驱动发起调用（`NodeStageVolume`, `NodePublishVolume`等），调用是通过UDS(Unix Domain Socket)完成的，用来mount以及umount volume。
    - Kubelet发现CSI驱动（以及用来与CSI驱动通信的UDS）的机制是通过[`kubelet plugin regisration mechanism`](https://kubernetes.io/docs/concepts/extend-kubernetes/compute-storage-net/device-plugins/#device-plugin-registration)机制完成的。
    - 因此，所有的CSI驱动都需要在支持的节点上注册自己。
* Master节点与CSI驱动的通信
    - Master组件并不直接（使用UDS）跟CSI驱动通信。
    - Master只与Kubernetes API互动，应该说的是通过K8s API与资源打交道。
    - 因此，CSI驱动需要监听资源的变化（volume create, volume attach, volume snapshot等），通过事件处理来处理任务。

### 推荐的部署以及开发方式
`推荐方式`包括以下几部分：
* CSI [Sidecar Containers](https://kubernetes-csi.github.io/docs/sidecar-containers.html)，选择需要的Sidecar Container，不需要的不用部署。
* CSI `objects`，CSI驱动引入的两个CRD: `CSIDriver`，`CSINode`，这两个在部署的时候一般自动配置。
* CSI `Driver Testing`工具
在上述方式下，开发者需要做的工作有：
1. 实现一个容器化的应用，实现`Identity`,`Node`服务，以及可选的`Controller`服务。
2. 使用`csi-sanity`来进行UT测试。
3. 定义Kubernetes API Yaml文件来部署CSI驱动，并选择适当的sidecar容器。参考下面的部署方式。
4. 部署，并进行e2e测试。

## 开发CSI驱动
第一步就是实现[CSI specification](https://github.com/container-storage-interface/spec/blob/master/spec.md#rpc-interface)中的gRPC接口。至少需要实现的接口是：
* CSI `Identity`服务
    - 让调用者（Kubernetes组件，以及sidecar容器）识别驱动，并且知道驱动提供了哪些功能。
* CSI `Node`服务
    - 只有`NodePublishVolume`，`NodeUnpublishVolume`，`NodeGetCapabilities`是required的。
    - 调用者通过required方法让volume在某个路径上可用，就是挂载吧，并发现驱动还提供了哪些额外的功能。
Kubelet代码中的csi-plugin会调用Node service中的服务，来实现Volume的挂载等。

### Kubernetes CSI Sidecar 容器
sidecar容器是社区维护的，为了方便开发，减少冗余代码，严格来说，这些sidecar容器都是可选的，但是推荐使用。社区提供的sidecar有：
* external-provisioner
* external-attacher
* external-snapshotter
* external-resizer
* node-driver-registrar
* cluster-driver-registrar (deprecated)
* livenessprobe

这里我们只看`external-provisioner`，其git仓库为：[https://github.com/kubernetes-csi/external-provisioner](https://github.com/kubernetes-csi/external-provisioner)

`external-provisioner`的作用是动态provision volume，其调用的接口是`CreateVolume`以及`DeleteVolume`，这两个都是Controller Service中的接口，volume的provision是通过监听PVC来实现的。如果PVC引用了`StorageClass`，并且`StorageClass`中的`Provisioner`字段与CSI驱动`GetPluginInfo`返回的一致（属于Identity Service中的接口），那么可以调用这个驱动的接口来实现provision了。一旦volume成功provision，这个sidecar容器创建一个PV容器来代表这个volume。

能够实现自动provision的容器需要使用这个sidecar容器，并声明有`CREATE_DELETE_VOLUME`controller capability.


## 在Kubernetes集群中部署CSI驱动
CSI驱动在集群中一般分两部分部署：一个Controller组件，以及部署在每个节点的pre-node组件。

### Controller组件
控制器组件可以以Deployment或者Statefulset的形式部署在集群的任何一个节点中，它包含CSI driver中实现了CSI Controller服务的那部分，以及一个或者多个sidecar容器。这些Controller服务，一般与K8s资源交互，并调用CSI driver的Controller service。**sidecar与CSI driver之间的通信是通过UDS进行的**，因为sidecar需要与k8s资源交互（PV的创建等），所以需要设置RBAC权限。

### Node plugin
Node组件需要以DaemonSet的形式部署在集群的各个节点中，Node组件需要实现CSI Node service以及需要包含一个`node-driver-registrar`sidecar容器。

Kubernetes Kubelet运行在每个节点上，负责调用`CSI Node service`接口，这些接口主要是mount以及unmount volume，使Pod能够使用这些volume，Kubelet与CSI driver的通信是靠UDS进行的，这个UDS必须以hostpath的形式挂到Node组件中，同时还有第二个uds，这个uds主要是用来实现`node-driver-registrar`sidecar与csi driver直接的通信。

下图是官方的架构设计，来自[https://github.com/kubernetes/community/blob/master/contributors/design-proposals/storage/container-storage-interface.md](https://github.com/kubernetes/community/blob/master/contributors/design-proposals/storage/container-storage-interface.md)
![java-javascript](/img/in-post/storage-csi/csi.png)

**Driver Volume Mounts**

Node plugin需要直接访问host的目录并挂载volume，CSI driver使用的挂载点必须设置为`Bidirectional`，这样container挂载目录时，Kubelet能看到。可以看下面的例子：
```yaml
      containers:
      - name: my-csi-driver
        ...
        volumeMounts:
        - name: socket-dir
          mountPath: /csi
        - name: mountpoint-dir
          mountPath: /var/lib/kubelet/pods
          mountPropagation: "Bidirectional"
      - name: node-driver-registrar
        ...
        volumeMounts:
        - name: registration-dir
          mountPath: /registration
      volumes:
      # This volume is where the socket for kubelet->driver communication is done
      - name: socket-dir
        hostPath:
          path: /var/lib/kubelet/plugins/<driver-name>
          type: DirectoryOrCreate
      # This volume is where the driver mounts volumes
      - name: mountpoint-dir
        hostPath:
          path: /var/lib/kubelet/pods
          type: Directory
      # This volume is where the node-driver-registrar registers the plugin
      # with kubelet
      - name: registration-dir
        hostPath:
          path: /var/lib/kubelet/plugins_registry
          type: Directory
```

**启用特权模式**

使用CSI driver，Kubernetes集群必须开始特权容器，（apiserver以及kubelet的`--allow-privileged`选项必须设置为true）