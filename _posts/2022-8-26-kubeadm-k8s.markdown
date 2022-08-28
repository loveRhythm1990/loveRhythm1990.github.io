---
layout:     post
title:      "使用 kubeadm 部署 Kubernetes"
date:       2022-8-26 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - k8s
---

这里使用 kubeadm 部署下 kubernetes，并配置 flannel 网络模型为 `host-gw`。kubeadm 的官方文档为[Creating a cluster with kubeadm](https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/create-cluster-kubeadm/)，这个文档还没好好研究，这里重点不是 kubeadm 的使用，使用 kubeadm 部署 kubenetes 细节很多。

测试系统为 centos，两台机器，master: 192.168.31.201; worker: 192.168.31.202.

### 前置条件
这里只记录会阻塞安装的前置条件，其他前置条件暂时不考虑。
#### 关闭防火墙
```s
sudo systemctl stop firewalld
sudo systemctl disable firewalld
```
这里遇到一个 case，如果安装时不关闭防火墙，使用 `kubeadm join` 时，会遇到 `connect: no route to host` 问题，所有的节点都需要关闭防火墙。

#### 关闭 swap
如果不关闭 `swap`，kubeadm 的前置条件不通过。
```s
sudo swapoff -a
# vi /etc/fstab，注释 swap 挂载
# #/dev/mapper/centos-swap swap                    swap    defaults        0 0
```

#### 配置 kubernetes 安装源
添加文件 `/etc/yum.repos.d/kubernetes.repo`，配置完，执行 `yum update` 更新一下。
```s
[kubernetes]
name=Kubernetes
baseurl=https://mirrors.aliyun.com/kubernetes/yum/repos/kubernetes-el7-x86_64/
enabled=1
gpgcheck=1
repo_gpgcheck=0
gpgkey=https://mirrors.aliyun.com/kubernetes/yum/doc/yum-key.gpg https://mirrors.aliyun.com/kubernetes/yum/doc/rpm-package-key.gpg
```
执行 `yum update` 时，遇到了 `https://packages.cloud.google.com/yum/repos/kubernetes-el7-x86_64/repodata/repomd.xml: [Errno -1] repomd.xml signature could not be verified for kubernetes` 错误，参考[Got "repomd.xml signature could not be verified for kubernetes" error when installing Kubernetes from yum repo on Amazon Linux 2 #60134](https://github.com/kubernetes/kubernetes/issues/60134)，更新了一下 `/etc/yum.repos.d/kubernetes.repo` 配置
```s
#repo_gpgcheck=1
repo_gpgcheck=0
```

### 部署 kubernetes

#### 安装 binary
这里安装 1.16 版本的 kubernetes，目前我们使用的也是这个版本。
```s
sudo yum install kubelet-1.16.9-0 kubeadm-1.16.9-0 kubectl-1.16.9-0
```

#### 替换镜像源
从 `k8s.gcr.io` 拉镜像比较慢，替换成阿里云的。首先看下需要的镜像
```s
[decent@HostGW-Master ~]$ kubeadm config images list
I0827 08:56:23.303670   95373 version.go:251] remote version is much newer: v1.25.0; falling back to: stable-1.16
k8s.gcr.io/kube-apiserver:v1.16.15
k8s.gcr.io/kube-controller-manager:v1.16.15
k8s.gcr.io/kube-scheduler:v1.16.15
k8s.gcr.io/kube-proxy:v1.16.15
k8s.gcr.io/pause:3.1
k8s.gcr.io/etcd:3.3.15-0
k8s.gcr.io/coredns:1.6.2
```
通过下面脚本从阿里云拉镜像并 tag 成上面的样子，这样就不用从 `k8s.gcr.io` 拉镜像了。配置 pull-k8s-images.sh 脚本如下：
```s
for i in `kubeadm config images list`; do
  imageName=${i#k8s.gcr.io/}
  docker pull registry.aliyuncs.com/google_containers/$imageName
  docker tag registry.aliyuncs.com/google_containers/$imageName k8s.gcr.io/$imageName
  docker rmi registry.aliyuncs.com/google_containers/$imageName
done;
```

同时修改 docker 的镜像源，拉镜像时，从阿里云拉，cat /etc/docker/daemon.json，参考[https://cr.console.aliyun.com/cn-hangzhou/instances/mirrors](https://cr.console.aliyun.com/cn-hangzhou/instances/mirrors)
```s
{
  "registry-mirrors": [
    "https://x2bhgwmy.mirror.aliyuncs.com",
    "https://hub-mirror.c.163.com"
  ]
}
```

#### 安装集群
通过下面命令安装 kubernetes，`pod-network-cidr` 的配置要跟 flannel 的配置一致。
```s
kubeadm init --pod-network-cidr=10.244.0.0/16
```
没有意外的话，安装成功，kubeadm 安装完成之后，配置的kubeconfig 文件位置为 `/etc/kubernetes/admin.conf`，需要拷贝到 ~/.kube/config。这个时候，node 还是 `NotReady` 状态，因为还没有配置 CNI 网络。

> 这里也介绍下重置 kubeadm 的方式，命令为 `kubeadm reset`，可能需要安装多次。

#### 配置 flannel
flannel 没有特殊配置，使用 host-gw 时，将 backend-type 改为 host-gw 即可，全部的yaml 文件参考文章末尾。
```s
  net-conf.json: |
    {
      "Network": "10.244.0.0/16",
      "Backend": {
        "Type": "host-gw"
      }
    }
```
配置完直接 `kubectl apply -f` 即可。这样集群就安装完成了。

### 参考
[阿里云镜像安装kubeadm和kubernetes](https://blog.51cto.com/u_15162069/2887315)

[Kubeadm join failed : Failed to request cluster-info: no route to host](https://www.anycodings.com/1questions/2311918/kubeadm-join-failed-failed-to-request-cluster-info)

[kubeadm 安装指定版本的 k8s 集群](https://segmentfault.com/a/1190000039144214)


kube-flannel 的 yaml 文件 参考[https://raw.githubusercontent.com/coreos/flannel/master/Documentation/kube-flannel.yml](https://raw.githubusercontent.com/coreos/flannel/master/Documentation/kube-flannel.yml) 相比于原来的文件，做了两处修改：
* 去掉了 `priorityClassName: system-node-critical`，不然在 1.16 的 K8s 里无法安装
* 把 flannel 的 backend type 改成了 `host-gw`，原来是 `vxlan`。


```yml
---
kind: Namespace
apiVersion: v1
metadata:
  name: kube-flannel
  labels:
    pod-security.kubernetes.io/enforce: privileged
---
kind: ClusterRole
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: flannel
rules:
- apiGroups:
  - ""
  resources:
  - pods
  verbs:
  - get
- apiGroups:
  - ""
  resources:
  - nodes
  verbs:
  - list
  - watch
- apiGroups:
  - ""
  resources:
  - nodes/status
  verbs:
  - patch
---
kind: ClusterRoleBinding
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: flannel
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: flannel
subjects:
- kind: ServiceAccount
  name: flannel
  namespace: kube-flannel
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: flannel
  namespace: kube-flannel
---
kind: ConfigMap
apiVersion: v1
metadata:
  name: kube-flannel-cfg
  namespace: kube-flannel
  labels:
    tier: node
    app: flannel
data:
  cni-conf.json: |
    {
      "name": "cbr0",
      "cniVersion": "0.3.1",
      "plugins": [
        {
          "type": "flannel",
          "delegate": {
            "hairpinMode": true,
            "isDefaultGateway": true
          }
        },
        {
          "type": "portmap",
          "capabilities": {
            "portMappings": true
          }
        }
      ]
    }
  net-conf.json: |
    {
      "Network": "10.244.0.0/16",
      "Backend": {
        "Type": "host-gw"
      }
    }
---
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: kube-flannel-ds
  namespace: kube-flannel
  labels:
    tier: node
    app: flannel
spec:
  selector:
    matchLabels:
      app: flannel
  template:
    metadata:
      labels:
        tier: node
        app: flannel
    spec:
      affinity:
        nodeAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            nodeSelectorTerms:
            - matchExpressions:
              - key: kubernetes.io/os
                operator: In
                values:
                - linux
      hostNetwork: true
      #priorityClassName: system-node-critical
      tolerations:
      - operator: Exists
        effect: NoSchedule
      serviceAccountName: flannel
      initContainers:
      - name: install-cni-plugin
       #image: flannelcni/flannel-cni-plugin:v1.1.0 for ppc64le and mips64le (dockerhub limitations may apply)
        image: docker.io/rancher/mirrored-flannelcni-flannel-cni-plugin:v1.1.0
        command:
        - cp
        args:
        - -f
        - /flannel
        - /opt/cni/bin/flannel
        volumeMounts:
        - name: cni-plugin
          mountPath: /opt/cni/bin
      - name: install-cni
       #image: flannelcni/flannel:v0.19.1 for ppc64le and mips64le (dockerhub limitations may apply)
        image: docker.io/rancher/mirrored-flannelcni-flannel:v0.19.1
        command:
        - cp
        args:
        - -f
        - /etc/kube-flannel/cni-conf.json
        - /etc/cni/net.d/10-flannel.conflist
        volumeMounts:
        - name: cni
          mountPath: /etc/cni/net.d
        - name: flannel-cfg
          mountPath: /etc/kube-flannel/
      containers:
      - name: kube-flannel
       #image: flannelcni/flannel:v0.19.1 for ppc64le and mips64le (dockerhub limitations may apply)
        image: docker.io/rancher/mirrored-flannelcni-flannel:v0.19.1
        command:
        - /opt/bin/flanneld
        args:
        - --ip-masq
        - --kube-subnet-mgr
        resources:
          requests:
            cpu: "100m"
            memory: "50Mi"
          limits:
            cpu: "100m"
            memory: "50Mi"
        securityContext:
          privileged: false
          capabilities:
            add: ["NET_ADMIN", "NET_RAW"]
        env:
        - name: POD_NAME
          valueFrom:
            fieldRef:
              fieldPath: metadata.name
        - name: POD_NAMESPACE
          valueFrom:
            fieldRef:
              fieldPath: metadata.namespace
        - name: EVENT_QUEUE_DEPTH
          value: "5000"
        volumeMounts:
        - name: run
          mountPath: /run/flannel
        - name: flannel-cfg
          mountPath: /etc/kube-flannel/
        - name: xtables-lock
          mountPath: /run/xtables.lock
      volumes:
      - name: run
        hostPath:
          path: /run/flannel
      - name: cni-plugin
        hostPath:
          path: /opt/cni/bin
      - name: cni
        hostPath:
          path: /etc/cni/net.d
      - name: flannel-cfg
        configMap:
          name: kube-flannel-cfg
      - name: xtables-lock
        hostPath:
          path: /run/xtables.lock
          type: FileOrCreate
```