---
layout:     post
title:      "etcd 常见运维"
date:       2021-08-28 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - etcd
---

关于证书相关配置参考：[《Transport security model》](https://etcd.io/docs/v3.5/op-guide/security/)，可以通过查看 etcd 的启动命令，来判断证书的位置，参考参数：
```s
--cert-file=<path>: Certificate used for SSL/TLS connections to etcd. When this option is set, advertise-client-urls can use the HTTPS schema.
--key-file=<path>: Key for the certificate. Must be unencrypted.
```

etcdctl 常见数据读写操作参考[Interacting with etcd](https://etcd.io/docs/v3.5/dev-guide/interacting_v3/)

### 基本操作

1. 查看 etcd 集群 metrics，加上 sudo 的原因是可能没有权限读取证书文件。

`sudo curl --cert /path/to/cert.pem --key /path/to/key.pem  https://<etcd-server>:2379/metrics -k`

2. 查看集群节点列表，这个需要带上 `--cacert` 参数，主要注意的是，etcd 有可能跟 k8s 集群共用一个 ca（这个取决于部署方式），另外 `write-out` 可以选择 json、sample、table 等。

`sudo ./etcdctl --endpoints=https://<etcd-server>:2379 --cert=/etc/kubernetes/ssl/kube-etcd-<host>.pem --key=/etc/kubernetes/ssl/kube-etcd-<host>-key.pem --cacert=/etc/kubernetes/ssl/ca.pem  member list --write-out="table"`

etcdctl 工具可以再 etcd release 中下载，比如：[https://github.com/etcd-io/etcd/releases/tag/v3.4.3](https://github.com/etcd-io/etcd/releases/tag/v3.4.3)

3. 获取 etcd 中所有的 key

`sudo ./etcdctl --endpoints=https://<etcd-server>:2379 --cert=/etc/kubernetes/ssl/kube-etcd-<host>.pem --key=/etc/kubernetes/ssl/kube-etcd-<host>-key.pem --cacert=/etc/kubernetes/ssl/ca.pem get / --prefix --keys-only`

4. 查看所有集群节点的状态，这个时候需要将集群中所有的节点列表都添加进去了，（可以找到 kube-apiserver 的启动参数 --etcd-servers 进行一键复制，这个参数也指定了所有的 etcd 节点列表）

`sudo ./etcdctl --endpoints=https://<etcd-server>:2379 --cert=/etc/kubernetes/ssl/kube-etcd-<host>.pem --key=/etc/kubernetes/ssl/kube-etcd-<host>-key.pem --cacert=/etc/kubernetes/ssl/ca.pem get / --prefix --keys-only`

输出如下，包含了节点的 ID，数据库大小，是不是 leader，raft term 以及 raft index 等。
```s
+----------------------------+------------------+---------+---------+-----------+------------+-----------+------------+--------------------+--------+
|          ENDPOINT          |        ID        | VERSION | DB SIZE | IS LEADER | IS LEARNER | RAFT TERM | RAFT INDEX | RAFT APPLIED INDEX | ERRORS |
+----------------------------+------------------+---------+---------+-----------+------------+-----------+------------+--------------------+--------+
| https://10.72.220.155:2379 |  b70953bfd222328 |   3.4.3 |  1.2 GB |     false |      false |      1455 | 1540134058 |         1540134058 |        |
| https://10.73.246.123:2379 | 5cc532cd335d29d2 |   3.4.3 |  1.2 GB |      true |      false |      1455 | 1540134058 |         1540134058 |        |
| https://10.73.247.217:2379 | b5b8154918511716 |   3.4.3 |  1.2 GB |     false |      false |      1455 | 1540134058 |         1540134058 |        |
+----------------------------+------------------+---------+---------+-----------+------------+-----------+------------+--------------------+--------+
```
5. 查看 etcd 是否健康
`ETCDCTL_API=3 etcdctl --cacert=/opt/kubernetes/ssl/ca.pem --cert=/opt/kubernetes/ssl/server.pem --key=/opt/kubernetes/ssl/server-key.pem --endpoints=https://192.168.1.36:2379,https://192.168.1.37:2379,https://192.168.1.38:2379 endpoint health`

### 备份以及恢复
备份以及恢复的思路，就是将之前的 etcd 全部数据拷贝一份，然后生成一个新的 etcd 集群，对于坏掉的 etcd 节点，踢掉后，找个新的节点加进去。
#### 备份
备份有两种方式：
* 把一个 etcd 节点的数据做个快照。这里需要一个节点就可以了，`snapshot.db` 就是生成的备份的名字。
```s
etcdctl --endpoints $ENDPOINT snapshot save snapshot.db
```
* 直接把数据库文件保存一下，数据库文件为：`<data-path>/member/snap/db`.

#### 恢复
在 K8s 集群中，如果需要恢复 Etcd，首先需要把所有的 kube-apiserver 都停掉。然后用`同一个备份文件`恢复所有的 Etcd，（这个时候，如果有 Etcd 的地址发生了变化，还要修改 kube-apiserver 的参数 --etcd-servers），然后再启动 Etcd。

1. 停止 Etcd
2. 移除 Etcd 存储目录下的数据
3. 拷贝 Etcd 备份到其他所有 Etcd 节点
4. 恢复
```s
$ ETCDCTL_API=3 etcdctl snapshot restore snapshot.db \
  --name m1 \
  --initial-cluster m1=http://host1:2380,m2=http://host2:2380,m3=http://host3:2380 \
  --initial-cluster-token etcd-cluster-1 \
  --initial-advertise-peer-urls http://host1:2380
$ ETCDCTL_API=3 etcdctl snapshot restore snapshot.db \
  --name m2 \
  --initial-cluster m1=http://host1:2380,m2=http://host2:2380,m3=http://host3:2380 \
  --initial-cluster-token etcd-cluster-1 \
  --initial-advertise-peer-urls http://host2:2380
$ ETCDCTL_API=3 etcdctl snapshot restore snapshot.db \
  --name m3 \
  --initial-cluster m1=http://host1:2380,m2=http://host2:2380,m3=http://host3:2380 \
  --initial-cluster-token etcd-cluster-1 \
  --initial-advertise-peer-urls http://host3:2380
```
5. 启动 Etcd
```s
$ etcd \
  --name m1 \
  --listen-client-urls http://host1:2379 \
  --advertise-client-urls http://host1:2379 \
  --listen-peer-urls http://host1:2380 &
$ etcd \
  --name m2 \
  --listen-client-urls http://host2:2379 \
  --advertise-client-urls http://host2:2379 \
  --listen-peer-urls http://host2:2380 &
$ etcd \
  --name m3 \
  --listen-client-urls http://host3:2379 \
  --advertise-client-urls http://host3:2379 \
  --listen-peer-urls http://host3:2380 &
```

### 参考
[Operating etcd clusters for Kubernetes](https://kubernetes.io/docs/tasks/administer-cluster/configure-upgrade-etcd/)

[Etcd Disaster recovery](https://etcd.io/docs/v3.3/op-guide/recovery/)

[Etcd v3备份与恢复](https://zhuanlan.zhihu.com/p/101523337)