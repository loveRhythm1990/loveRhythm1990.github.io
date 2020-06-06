---
layout:     post
title:      "k8s csi 概述"
date:       2020-06-06 10:10:00
author:     "weak old dog"
header-img-credit: false
tags:
    - k8s
    - volume
---

[官方文档](https://kubernetes-csi.github.io/docs/)阅读笔记，内容分为概述、部署、一些controller service等。目标是了解csi大体架构，如果有需要进行开发，可以直接拿来进行开发，不需要再进行阅读文档。

#### 介绍
这部分主要介绍了csi的通信方式，分下面两部分
* Kubelet与CSI驱动通信
    - Kubelet直接向CSI驱动发起调用（`NodeStageVolume`, `NodePublishVolume`等），调用是通过UDS(Unix Domain Socket)完成的，用来mount以及umount volume。
    - Kubelet发现CSI驱动（以及用来与CSI驱动通信的UDS）的机制是通过[`kubelet plugin regisration mechanism`](https://kubernetes.io/docs/concepts/extend-kubernetes/compute-storage-net/device-plugins/#device-plugin-registration)机制完成的。
    - 因此，所有的CSI驱动都需要在支持的节点上注册自己。
* Master节点与CSI驱动的通信
    - Master组件并不直接（使用UDS）跟CSI驱动通信。
    - Master只与Kubernetes API互动，应该说的是通过K8s API与资源打交道。
    - 因此，CSI驱动需要监听资源的变化（volume create, volume attach, volume snapshot等），通过事件处理来处理任务。

###### 推荐的部署以及开发方式
`推荐方式`包括以下几部分：
* CSI [Sidecar Containers](https://kubernetes-csi.github.io/docs/sidecar-containers.html)
* CSI `objects`，CSI驱动引入的两个CRD: `CSIDriver`，`CSINode`
* CSI `Driver Testing`工具
在上述方式下，开发者需要做的工作有：
1. 实现一个容器化的应用，实现`Identity`,`Node`服务，以及可选的`Controller`服务。
2. 使用`csi-sanity`来进行UT测试。
3. 定义Kubernetes API Yaml文件来部署CSI驱动，并选择适当的sidecar容器。参考下面的部署方式。
4. 部署，并进行e2e测试。

#### 开发CSI驱动
第一步就是实现[CSI specification](https://github.com/container-storage-interface/spec/blob/master/spec.md#rpc-interface)中的gRPC接口。至少需要实现的接口是：
* CSI `Identity`服务
    - 让调用者（Kubernetes组件，以及sidecar容器）识别驱动，并且知道驱动提供了哪些功能。
`Identity`服务一共有三个接口，定义如下，`GetPluginCapabilities`返回该plugin拥有的capability。

```
service Identity {
  rpc GetPluginInfo(GetPluginInfoRequest)
    returns (GetPluginInfoResponse) {}

  rpc GetPluginCapabilities(GetPluginCapabilitiesRequest)
    returns (GetPluginCapabilitiesResponse) {}

  rpc Probe (ProbeRequest)
    returns (ProbeResponse) {}
}
```

* CSI `Node`服务
    - 只有`NodePublishVolume`，`NodeUnpublishVolume`，`NodeGetCapabilities`是required的。
    - 调用者通过required方法让volume在某个路径上可用，就是挂载吧，并发现驱动还提供了哪些额外的功能。
Kubelet代码中的csi-plugin会调用Node service中的服务，来实现Volume的挂载等。

###### Kubernetes CSI Sidecar 容器
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


#### 在Kubernetes集群中部署CSI驱动
CSI驱动在集群中一般分两部分部署：一个Controller组件，以及部署在每个节点的pre-node组件。

###### Controller组件
控制器组件可以以Deployment或者Statefulset的形式部署在集群的任何一个节点中，它包含CSI driver中实现了CSI Controller服务的那部分，以及一个或者多个sidecar容器。这些Controller服务，一般与K8s资源交互，并调用CSI driver的Controller service。**sidecar与CSI driver之间的通信是通过UDS进行的**，因为sidecar需要与k8s资源交互（PV的创建等），所以需要设置RBAC权限。

###### Node plugin
Node组件需要以DaemonSet的形式部署在集群的各个节点中，Node组件需要实现CSI Node service以及需要包含一个`node-driver-registrar`sidecar容器。

Kubernetes Kubelet运行在每个节点上，负责调用`CSI Node service`接口，这些接口主要是mount以及unmount volume，使Pod能够使用这些volume，Kubelet与CSI driver的通信是靠UDS进行的，这个UDS必须以hostpath的形式挂到Node组件中，同时还有第二个uds，这个uds主要是用来实现`node-driver-registrar`sidecar与csi driver直接的通信。

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