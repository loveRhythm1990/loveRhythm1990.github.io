---
layout:     post
title:      "Etcd 集群运维基础"
date:       2021-08-28 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - etcd
---

关于证书相关配置参考：[《Transport security model》](https://etcd.io/docs/v3.5/op-guide/security/)，可以通过查看 etcd 的启动命令，来判断证书的位置，参考参数：
* --cert-file=`<path>`: Certificate used for SSL/TLS connections to etcd. When this option is set, advertise-client-urls can use the HTTPS schema
* --key-file=`<path>`: Key for the certificate. Must be unencrypted.`

etcdctl 常见数据读写操作参考[Interacting with etcd](https://etcd.io/docs/v3.5/dev-guide/interacting_v3/)

### 基本操作

a. 查看 etcd 集群 metrics，加上 sudo 的原因是可能没有权限读取证书文件。
```s
sudo curl --cert /path/to/cert.pem \
--key /path/to/key.pem  \
https://<etcd-server>:2379/metrics -k
```

b. 查看集群节点列表，这个需要带上 `--cacert` 参数，主要注意的是，etcd 有可能跟 k8s 集群共用一个 ca（这个取决于部署方式），另外 `write-out` 可以选择 json、sample、table 等。
```s
sudo ./etcdctl --endpoints=https://<etcd-server>:2379 \
--cert=/etc/kubernetes/ssl/kube-etcd-<host>.pem \
--key=/etc/kubernetes/ssl/kube-etcd-<host>-key.pem \
--cacert=/etc/kubernetes/ssl/ca.pem  \
member list --write-out="table"
```

etcdctl 工具可以再 etcd release 中下载，比如：[https://github.com/etcd-io/etcd/releases/tag/v3.4.3](https://github.com/etcd-io/etcd/releases/tag/v3.4.3)

c. 获取 etcd 中所有的 key
```s
sudo ./etcdctl --endpoints=https://<etcd-server>:2379 \
--cert=/etc/kubernetes/ssl/kube-etcd-<host>.pem \
--key=/etc/kubernetes/ssl/kube-etcd-<host>-key.pem \
--cacert=/etc/kubernetes/ssl/ca.pem get / --prefix --keys-only
```
d. 查看所有集群节点的状态，这个时候需要将集群中所有的节点列表都添加进去了，（可以找到 kube-apiserver 的启动参数 --etcd-servers 进行一键复制，这个参数也指定了所有的 etcd 节点列表）
```s
sudo ./etcdctl --endpoints=https://<etcd-server>:2379 \
--cert=/etc/kubernetes/ssl/kube-etcd-<host>.pem \
--key=/etc/kubernetes/ssl/kube-etcd-<host>-key.pem \
--cacert=/etc/kubernetes/ssl/ca.pem \
endpoint status --write-out=table
```
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
e. 查看 etcd 是否健康
```s
ETCDCTL_API=3 etcdctl --cacert=/opt/kubernetes/ssl/ca.pem \
--cert=/opt/kubernetes/ssl/server.pem \
--key=/opt/kubernetes/ssl/server-key.pem \
--endpoints=https://192.168.1.36:2379,https://192.168.1.37:2379,https://192.168.1.38:2379 \
endpoint health
```

f. Compact Etcd
Compact 对 etcd 数据没有影响，下面两条命令一起执行。
```s
rev=$(sudo etcdctl --endpoints=https://host1:2379 \
--cert=/etc/kubernetes/ssl/kube-etcd-host1.pem \
--key=/etc/kubernetes/ssl/kube-etcd-host1-key.pem \
--cacert=/etc/kubernetes/ssl/kube-ca.pem \
endpoint status --write-out="json" | egrep -o '"revision":[0-9]*' | egrep -o '[0-9].*')`

sudo etcdctl --endpoints=https://host1:2379 \
--cert=/etc/kubernetes/ssl/kube-etcd-host1.pem \
--key=/etc/kubernetes/ssl/kube-etcd-host1-key.pem \
--cacert=/etc/kubernetes/ssl/kube-ca.pem \
compact $rev
```
h. 清理碎片
compact 会产生碎片，defrag 清理碎片，etcd 磁盘容量较大时可用，参考文档[defrag](https://etcd.io/docs/v3.2/op-guide/maintenance/#defragmentation)
```s
sudo etcdctl --endpoints=https://host1:2379 \
--cert=/etc/kubernetes/ssl/kube-etcd-host1.pem \
--key=/etc/kubernetes/ssl/kube-etcd-host1-key.pem \
--cacert=/etc/kubernetes/ssl/kube-ca.pem \
defrag
```

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

#### 从数据库文件恢复
etcd 的数据文件路径为 `member/snap/db`，也可以通过此文件来恢复 etcd 集群，假设目前我们有一个 db 文件，想通过这个文件来恢复出三个节点的 etcd 集群，其操作步骤如下，以 goreman 为例。

a.首先我们有一个 etcd 集群，我们向里面写入一些数据。其数据文件放在了：/Users/decent/etcd/restore/m1.etcd/member/snap/db
```s
$ etcdctl put hello world519 –-endpoints=http://127.0.0.1:2379
$ etcdctl get hello --endpoints=http://127.0.0.1:2379
hello
world519
```
b. 现在我们假设这个 etcd 故障了，我们直接 ctrl+C 这个进程。然后通过下面命令恢复出三个 etcd 集群，因为数据库文件没有hash，所以要添加 `--skip-hash-check=true`，跳过一致性 hash 检查
```s
ETCDCTL_API=3 etcdctl snapshot restore /Users/decent/etcd/restore/m1.etcd/member/snap/db --name m1 --initial-cluster m1=http://127.0.0.1:2380,m2=http://127.0.0.1:23802,m3=http://127.0.0.1:23803 --initial-cluster-token etcd-cluster-example --initial-advertise-peer-urls http://127.0.0.1:2380 --skip-hash-check=true

ETCDCTL_API=3 etcdctl snapshot restore /Users/decent/etcd/restore/m1.etcd/member/snap/db --name m2 --initial-cluster m1=http://127.0.0.1:2380,m2=http://127.0.0.1:23802,m3=http://127.0.0.1:23803 --initial-cluster-token etcd-cluster-example --initial-advertise-peer-urls http://127.0.0.1:23802 --skip-hash-check=true

ETCDCTL_API=3 etcdctl snapshot restore /Users/decent/etcd/restore/m1.etcd/member/snap/db --name m3 --initial-cluster m1=http://127.0.0.1:2380,m2=http://127.0.0.1:23802,m3=http://127.0.0.1:23803 --initial-cluster-token etcd-cluster-example --initial-advertise-peer-urls http://127.0.0.1:23803 --skip-hash-check=true
```

c. 启动 etcd
```s
etcd --name m1 --listen-client-urls http://127.0.0.1:2379 --advertise-client-urls http://127.0.0.1:2379 --listen-peer-urls http://127.0.0.1:2380 &

etcd --name m2 --listen-client-urls http://127.0.0.1:23792 --advertise-client-urls http://127.0.0.1:23792 --listen-peer-urls http://127.0.0.1:23802 &

etcd --name m3 --listen-client-urls http://127.0.0.1:23793 --advertise-client-urls http://127.0.0.1:23793 --listen-peer-urls http://127.0.0.1:23803 &
```

d. 查看数据以及 endpoints 状态
```s
decent@Mac ~/e/r/cluster> etcdctl --endpoints=http://127.0.0.1:2379,http://127.0.0.1:23792,http://127.0.0.1:23793  endpoint status -w table
+------------------------+------------------+---------+---------+-----------+------------+-----------+------------+--------------------+--------+
|        ENDPOINT        |        ID        | VERSION | DB SIZE | IS LEADER | IS LEARNER | RAFT TERM | RAFT INDEX | RAFT APPLIED INDEX | ERRORS |
+------------------------+------------------+---------+---------+-----------+------------+-----------+------------+--------------------+--------+
|  http://127.0.0.1:2379 |  ee6f4291b9a87a3 |   3.4.3 |   29 kB |      true |      false |         2 |          8 |                  8 |        |
| http://127.0.0.1:23792 | 544c86a797494ec2 |   3.4.3 |   29 kB |     false |      false |         2 |          8 |                  8 |        |
| http://127.0.0.1:23793 | a0d7b0a318001c20 |   3.4.3 |   29 kB |     false |      false |         2 |          8 |                  8 |        |
+------------------------+------------------+---------+---------+-----------+------------+-----------+------------+--------------------+--------+

decent@Mac ~/e/r/cluster> etcdctl --endpoints=http://127.0.0.1:2379,http://127.0.0.1:23792,http://127.0.0.1:23793 get hello
hello
world519
```

### 参考
[Operating etcd clusters for Kubernetes](https://kubernetes.io/docs/tasks/administer-cluster/configure-upgrade-etcd/)

[Etcd Disaster recovery](https://etcd.io/docs/v3.3/op-guide/recovery/)

[Etcd v3备份与恢复](https://zhuanlan.zhihu.com/p/101523337)