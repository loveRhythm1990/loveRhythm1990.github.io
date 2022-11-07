---
layout:     post
title:      "通过Learner节点实现Etcd集群实时备份-以rke环境为例"
date:       2022-6-1 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - etcd
---

### 思路
因为同城机房的时延较小，大概在 1ms 左右，整体方案是在两个机房部署同一个 Etcd 集群，即：Etcd 集群的部分节点在 A 机房，另一部分节点在 B 机房（这个方案称为`方案一`）。同时有个兜底手段是，如果两个机房的时延确实造成了一些问题，那么需要升级为`方案二`，即：将 Etcd 集群部署到同一个机房A，同时在 B 机房添加一个 learner，进行实时备份。

> 2022.06.24 更新，通过目前的调研，Etcd 在同城部署下，时延的问题应该不是很大。

### 集群初始状态
K8s 集群信息：
```s
[decent@master1 rke_cluster]$ kubectl get nodes -o wide
NAME      STATUS   ROLES                      AGE     VERSION   INTERNAL-IP      EXTERNAL-IP   OS-IMAGE                KERNEL-VERSION           CONTAINER-RUNTIME
master1   Ready    controlplane,etcd,worker   5d13h   v1.20.5   192.168.31.101   <none>        CentOS Linux 7 (Core)   3.10.0-1160.el7.x86_64   docker://20.10.16
master2   Ready    controlplane,etcd,worker   5d13h   v1.20.5   192.168.31.102   <none>        CentOS Linux 7 (Core)   3.10.0-1160.el7.x86_64   docker://20.10.16
master3   Ready    controlplane,etcd,worker   52m     v1.20.5   192.168.31.103   <none>        CentOS Linux 7 (Core)   3.10.0-1160.el7.x86_64   docker://20.10.13
node1     Ready    etcd,worker                27m     v1.20.5   192.168.31.104   <none>        CentOS Linux 7 (Core)   3.10.0-1160.el7.x86_64   docker://20.10.13
node2     Ready    worker                     5d13h   v1.20.5   192.168.31.105   <none>        CentOS Linux 7 (Core)   3.10.0-1160.el7.x86_64   docker://20.10.13
```

etcd 集群信息
```s
[decent@master1 rke_cluster]$ sudo etcdctl --endpoints=https://192.168.31.101:2379,https://192.168.31.102:2379,https://192.168.31.103:2379,https://192.168.31.104:2379 --cert=/etc/kubernetes/ssl/kube-etcd-192-168-31-101.pem --key=/etc/kubernetes/ssl/kube-etcd-192-168-31-101-key.pem --cacert=/etc/kubernetes/ssl/kube-ca.pem endpoint status --write-out=table
[sudo] password for decent: 
+-----------------------------+------------------+---------+---------+-----------+------------+-----------+------------+--------------------+--------+
|          ENDPOINT           |        ID        | VERSION | DB SIZE | IS LEADER | IS LEARNER | RAFT TERM | RAFT INDEX | RAFT APPLIED INDEX | ERRORS |
+-----------------------------+------------------+---------+---------+-----------+------------+-----------+------------+--------------------+--------+
| https://192.168.31.101:2379 | 79d5043c5c6f48d3 |  3.4.14 |  3.9 MB |     false |      false |        35 |       6873 |               6873 |        |
| https://192.168.31.102:2379 | affd5634331a4b9f |  3.4.14 |  3.9 MB |     false |      false |        35 |       6873 |               6873 |        |
| https://192.168.31.103:2379 | dc04862fb7c44430 |  3.4.14 |  3.8 MB |      true |      false |        35 |       6873 |               6873 |        |
| https://192.168.31.104:2379 | 1fa43009ad7f5198 |  3.4.14 |  3.8 MB |     false |      false |        35 |       6873 |               6873 |        |
+-----------------------------+------------------+---------+---------+-----------+------------+-----------+------------+--------------------+--------+
```

简化模型，机房A 包含三个节点：master1、master2、node1；机房B 包含三个节点：master3、node2。其中 master1-3 以及 node1 都部署了 etcd。同时集群也部署了一些业务：
```s
[decent@master1 rke_cluster]$ kubectl get pods -o wide
NAME                READY   STATUS    RESTARTS   AGE   IP            NODE      NOMINATED NODE   READINESS GATES
hello-world-57xx7   1/1     Running   0          12m   10.42.1.31    master2   <none>           <none>
hello-world-khkp6   1/1     Running   0          12m   10.42.3.104   master1   <none>           <none>
```


### 将 master3 节点上的 etcd 变成 learner
因为 master3 部署在了 B 机房，现在假设 B 机房时延太大，需要将该节点的 etcd 实例删除。
【一开始部署了四个 etcd 实例，后面又通过 etcdctl 删掉了一个，之所以这么做，是因为 rke 会帮我们生成证书，否则需要我们自己通过 kube.ca 生成证书和密钥，这算是偷懒的一步，在实际操作中，当然是自己通过 ca 生成证书，而不是通过这种不优雅的方式。】

#### 通过 etcdctl 移除 master3 上的 etcd
通过上面的 etcdctl member status 命令可以看到 master3 上的 etcd 实例的 ID 为：`dc04862fb7c44430`。通过下面命令移除。
```s
[decent@master1 rke_cluster]$ sudo etcdctl --endpoints=https://192.168.31.101:2379 --cert=/etc/kubernetes/ssl/kube-etcd-192-168-31-101.pem --key=/etc/kubernetes/ssl/kube-etcd-192-168-31-101-key.pem --cacert=/etc/kubernetes/ssl/kube-ca.pem member remove dc04862fb7c44430
Member dc04862fb7c44430 removed from cluster e352421d354f9057
```

再看下整个 etcd 集群的状态，etcd-master3 已经被移除了。
```s
[decent@master1 rke_cluster]$ sudo etcdctl --endpoints=https://192.168.31.101:2379 --cert=/etc/kubernetes/ssl/kube-etcd-192-168-31-101.pem --key=/etc/kubernetes/ssl/kube-etcd-192-168-31-101-key.pem --cacert=/etc/kubernetes/ssl/kube-ca.pem member list
[sudo] password for decent: 
1fa43009ad7f5198, started, etcd-node1, https://192.168.31.104:2380, https://192.168.31.104:2379, false
79d5043c5c6f48d3, started, etcd-master1, https://192.168.31.101:2380, https://192.168.31.101:2379, false
affd5634331a4b9f, started, etcd-master2, https://192.168.31.102:2380, https://192.168.31.102:2379, false
```

这个时候还有很重要一步，**移除 master3 节点上的 etcd**，否则其会一直重启（并且其他 etcd 也会报错 `rafthttp: rejected the stream from peer dc04862fb7c44430 since it was removed`），命令如下，移除之后，观察几分钟，发现其并不会重启。
```s
docker stop etcd
docker rm etcd
docker stop etcd-rolling-snapshots
docker rm etcd-rolling-snapshots
```
我们也可以用 `docker rename` 命令来备份一下这两个容器。

#### 手动在 master3 节点添加一个 learner 节点
参考官方文档 [How to Add and Remove Members](https://etcd.io/docs/v3.5/tutorials/how-to-deal-with-membership/) 在 master1 节点执行添加操作，命令为：
```s
sudo etcdctl --endpoints=https://192.168.31.101:2379 \
    --cert=/etc/kubernetes/ssl/kube-etcd-192-168-31-101.pem \
    --key=/etc/kubernetes/ssl/kube-etcd-192-168-31-101-key.pem \
    --cacert=/etc/kubernetes/ssl/kube-ca.pem \
	member add --learner learner1 --peer-urls=https://192.168.31.103:2380
```
添加的新的 etcd learner 名字称为 `learner1`，注意命令中的`--initial-cluser` 参数，name 以及 url 不要填错了，填错了会添加不成功，且没有明显报错。

然后需要在 192.168.31.103 节点，也就是 master3 节点执行启动 etcd 命令，【注意，在实际 etcd learner 部署中，learner 也要以容器的形式部署，这里只是作为一个普通进程重启的】
```s
CLUSTER=etcd-master1=https://192.168.31.101:2380,etcd-master2=https://192.168.31.102:2380,learner1=https://192.168.31.103:2380,etcd-node1=https://192.168.31.104:2380

sudo etcd --data-dir=/var/lib/etcd --name=learner1 \
	--initial-advertise-peer-urls=https://192.168.31.103:2380 \
	--listen-peer-urls=https://192.168.31.103:2380 \
	--advertise-client-urls=https://192.168.31.103:2379 \
	--listen-client-urls=https://192.168.31.103:2379 \
	--initial-cluster=${CLUSTER} \
	--initial-cluster-state=existing \
	--peer-trusted-ca-file=/etc/kubernetes/ssl/kube-ca.pem \
    --peer-key-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-103-key.pem \
    --enable-v2=true \
    --initial-cluster-token=etcd-cluster-1 \
    --cert-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-103.pem \
    --peer-client-cert-auth=true \
    --client-cert-auth=true \
    --election-timeout=5000 \
    --trusted-ca-file=/etc/kubernetes/ssl/kube-ca.pem \
    --key-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-103-key.pem \
    --peer-cert-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-103.pem \
    --heartbeat-interval=500 
```

添加完了之后，再看下 etcd 集群列表，都是正常的。
```s
sudo etcdctl --endpoints=https://192.168.31.101:2379 member list
1fa43009ad7f5198, started, etcd-node1, https://192.168.31.104:2380, https://192.168.31.104:2379, false
48868bd7b7d8c715, started, learner1, https://192.168.31.103:2380, https://192.168.31.103:2379, true
79d5043c5c6f48d3, started, etcd-master1, https://192.168.31.101:2380, https://192.168.31.101:2379, false
affd5634331a4b9f, started, etcd-master2, https://192.168.31.102:2380, https://192.168.31.102:2379, false

[decent@master1 rke_cluster]$  sudo etcdctl --endpoints=https://192.168.31.101:2379,https://192.168.31.102:2379,https://192.168.31.103:2379,https://192.168.31.104:2379 \
endpoint status --write-out=table
+-----------------------------+------------------+---------+---------+-----------+------------+-----------+------------+--------------------+--------+
|          ENDPOINT           |        ID        | VERSION | DB SIZE | IS LEADER | IS LEARNER | RAFT TERM | RAFT INDEX | RAFT APPLIED INDEX | ERRORS |
+-----------------------------+------------------+---------+---------+-----------+------------+-----------+------------+--------------------+--------+
| https://192.168.31.101:2379 | 79d5043c5c6f48d3 |  3.4.14 |  4.1 MB |      true |      false |        37 |      19264 |              19264 |        |
| https://192.168.31.102:2379 | affd5634331a4b9f |  3.4.14 |  4.0 MB |     false |      false |        37 |      19264 |              19264 |        |
| https://192.168.31.103:2379 | 48868bd7b7d8c715 |   3.5.4 |  3.8 MB |     false |       true |        37 |      19264 |              19264 |        |
| https://192.168.31.104:2379 | 1fa43009ad7f5198 |  3.4.14 |  3.8 MB |     false |      false |        37 |      19264 |              19264 |        |
+-----------------------------+------------------+---------+---------+-----------+------------+-----------+------------+--------------------+--------+
```

#### 模拟故障
A 机房三个节点：master1、master2、node1 断电。

#### master3 上的 learner 恢复成单节点集群
在之前的实验中，已经知道，learner 节点无法通过 `--force-new-cluster` 恢复成一个单节点的集群（因为其没有 voter，即投票节点）。现在尝试下通过 `etcdctl snapshot restore` 命令恢复下集群。

先停掉之前的 leaner 进程，用下面命令恢复集群，注意下面两个命令参数都要保持一致（包括`--name`，url 以及 token 等）。别忘了还有 `--skip-hash-check`，因为我们是从数据库文件恢复集群，而不是从快照，需要跳过 hash 检查。
```s
etcdctl snapshot restore /var/lib/etcd/member/snap/db  --name=new-cluster --initial-cluster=new-cluster=https://192.168.31.103:2380 --initial-cluster-token=cluster-restore1 --initial-advertise-peer-urls=https://192.168.31.103:2380 --skip-hash-check=true
```
用下面命令启动集群。
```s
CLUSTER=new-cluster=https://192.168.31.103:2380

etcd --data-dir=/var/lib/etcd --name=new-cluster \
	--initial-advertise-peer-urls=https://192.168.31.103:2380 \
	--listen-peer-urls=https://192.168.31.103:2380 \
	--advertise-client-urls=https://192.168.31.103:2379 \
	--listen-client-urls=https://192.168.31.103:2379 \
	--initial-cluster=${CLUSTER} \
	--initial-cluster-state=new \
	--peer-trusted-ca-file=/etc/kubernetes/ssl/kube-ca.pem \
    --peer-key-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-103-key.pem \
    --enable-v2=true \
    --initial-cluster-token=cluster-restore1 \
    --cert-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-103.pem \
    --peer-client-cert-auth=true \
    --client-cert-auth=true \
    --election-timeout=5000 \
    --trusted-ca-file=/etc/kubernetes/ssl/kube-ca.pem \
    --key-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-103-key.pem \
    --peer-cert-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-103.pem \
    --heartbeat-interval=500 
```

#### 查看集群状态
在查看集群状态之前，需要将 K8s 组件：kube-apiserver、kube-controller-manager、kube-scheduler、kubelet、kube-proxy 重启一下。

通过下面命令看，节点状态是正确的。不过 master2 节点已经被断电了，节点状态也一直是 NotReady，上面的 pod 始终是 running 状态，这个貌似不太符合预期。（经过在 K8s 1.16上验证，这个最终应该能达到符合预期的状态）

我删除了一个 pod `hello-world-lwvvm`，之后他一直是 `Terminating` 状态，因为无法联系其节点上的 kubelet 执行删除操作，这个符合预期。同时启动了一个新的 pod `hello-world-hvslg`，说明集群是可读写的。
```s
[decent@master3 rke]$ kubectl get nodes
NAME      STATUS     ROLES                      AGE     VERSION
master1   NotReady   controlplane,etcd,worker   5d16h   v1.20.5
master2   NotReady   controlplane,etcd,worker   5d16h   v1.20.5
master3   Ready      controlplane,etcd,worker   3h33m   v1.20.5
node1     NotReady   etcd,worker                3h8m    v1.20.5
node2     Ready      worker                     5d16h   v1.20.5
[decent@master3 rke]$ kubectl get pods -o wide
NAME                READY   STATUS        RESTARTS   AGE    IP           NODE      NOMINATED NODE   READINESS GATES
hello-world-hvslg   1/1     Running       0          2m6s   10.42.2.2    node2     <none>           <none>
hello-world-lwvvm   1/1     Terminating   0          117m   10.42.1.33   master2   <none>           <none>
hello-world-v2cdq   1/1     Running       0          117m   10.42.1.34   master2   <none>           <none>
```