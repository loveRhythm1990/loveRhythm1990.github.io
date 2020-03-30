---
layout:     post
title:      "k8s存储的一些零碎知识"
date:       2020-03-30 10:10:00
author:     "weak old dog"
header-img-credit: false
tags:
    - k8s
    - 存储
---

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



#### 参考
[Cgroup - Linux 的 IO 资源隔离](https://www.v2ex.com/t/251497)

[k8s提供的local volume的static provisioner](https://github.com/kubernetes-sigs/sig-storage-local-static-provisioner)