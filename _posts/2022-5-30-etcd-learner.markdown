---
layout:     post
title:      "当我尝试在rke环境下通过Learner实现Etcd热备"
date:       2022-5-30 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - etcd
---

### 集群基础信息
首先我们有一个 K8s 集群，通过 rke 部署的。
```s
[decent@master1 ~]$ kubectl get nodes -o wide
NAME      STATUS   ROLES                      AGE   VERSION   INTERNAL-IP      EXTERNAL-IP   OS-IMAGE                KERNEL-VERSION           CONTAINER-RUNTIME
master1   Ready    controlplane,etcd,worker   13m   v1.20.5   192.168.31.101   <none>        CentOS Linux 7 (Core)   3.10.0-1160.el7.x86_64   docker://20.10.16
master2   Ready    controlplane,etcd,worker   13m   v1.20.5   192.168.31.102   <none>        CentOS Linux 7 (Core)   3.10.0-1160.el7.x86_64   docker://20.10.16
master3   Ready    controlplane,etcd,worker   13m   v1.20.5   192.168.31.103   <none>        CentOS Linux 7 (Core)   3.10.0-1160.el7.x86_64   docker://20.10.13
node1     Ready    worker                     13m   v1.20.5   192.168.31.104   <none>        CentOS Linux 7 (Core)   3.10.0-1160.el7.x86_64   docker://20.10.13
node2     Ready    worker                     13m   v1.20.5   192.168.31.105   <none>        CentOS Linux 7 (Core)   3.10.0-1160.el7.x86_64   docker://20.10.13
```

通过 etcdctl 查看 etcd 集群信息
```s
[decent@master1 ~]$ sudo etcdctl --endpoints=https://192.168.31.101:2379  member list --write-out=table
+------------------+---------+--------------+-----------------------------+-----------------------------+------------+
|        ID        | STATUS  |     NAME     |         PEER ADDRS          |        CLIENT ADDRS         | IS LEARNER |
+------------------+---------+--------------+-----------------------------+-----------------------------+------------+
| 178e111d1072acde | started | etcd-master3 | https://192.168.31.103:2380 | https://192.168.31.103:2379 |      false |
| 79d5043c5c6f48d3 | started | etcd-master1 | https://192.168.31.101:2380 | https://192.168.31.101:2379 |      false |
| affd5634331a4b9f | started | etcd-master2 | https://192.168.31.102:2380 | https://192.168.31.102:2379 |      false |
+------------------+---------+--------------+-----------------------------+-----------------------------+------------+
```

`member list` 命令输出的信息有限（但只需要指定一个`endpoint`），我们通过`endpoint status` 命令来查看，`--endpoints` 指定三个节点，证书使用一个节点就可以了。
```s
[decent@master1 ~]$ sudo etcdctl --endpoints=https://192.168.31.101:2379,https://192.168.31.102:2379,https://192.168.31.103:2379 \
endpoint status --write-out=table
+-----------------------------+------------------+---------+---------+-----------+------------+-----------+------------+--------------------+--------+
|          ENDPOINT           |        ID        | VERSION | DB SIZE | IS LEADER | IS LEARNER | RAFT TERM | RAFT INDEX | RAFT APPLIED INDEX | ERRORS |
+-----------------------------+------------------+---------+---------+-----------+------------+-----------+------------+--------------------+--------+
| https://192.168.31.101:2379 | 79d5043c5c6f48d3 |  3.4.14 |  3.8 MB |      true |      false |         2 |       6683 |               6683 |        |
| https://192.168.31.102:2379 | affd5634331a4b9f |  3.4.14 |  3.8 MB |     false |      false |         2 |       6683 |               6683 |        |
| https://192.168.31.103:2379 | 178e111d1072acde |  3.4.14 |  3.8 MB |     false |      false |         2 |       6683 |               6683 |        |
+-----------------------------+------------------+---------+---------+-----------+------------+-----------+------------+--------------------+--------+
```

### 添加 learner 节点
在添加 learner 节点之前，我们首先需要生成这个节点的证书，生成证书的过程参考[《Generate Certificates Manually》](https://kubernetes.io/docs/tasks/administer-cluster/certificates/)，以及【[《Golang TLS编程》](https://loverhythm1990.github.io/2021/05/06/golang-tls/)。因为我们是通过 rancher 拉起的 K8s 集群，ca 证书已经存在，所以只需要通过 csr 向 ca 生成 learner 证书以及密钥即可。rancher 证书放置的目录为：
```s
/etc/kubernetes/ssl/kube-ca.pem
/etc/kubernetes/ssl/kube-ca-key.pem
```

#### 生成 Learner 证书
a. 首先生成一个密钥，生成密钥不需要任何输入
```s
openssl genrsa -out kube-etcd-192-168-31-104-key.pem 2048
```

b. 然后通过密钥 kube-etcd-192-168-31-104-key.pem 生成一个 CSR，CSR 配置文件如下，根据 [How to create a CSR with OpenSSL](https://www.switch.ch/pki/manage/request/csr-openssl/), `CN` 字段是必须设置的（这篇文章说是要填域名，但是K8s文档填的是IP）。假设这个配置文件名为：csr.conf。
```s
[ req ]
default_bits = 2048
prompt = no
default_md = sha256
req_extensions = req_ext
distinguished_name = dn

[ dn ]
C = CN
ST = SH
O = MT
CN = 192.168.31.104

[ req_ext ]
subjectAltName = @alt_names

[ alt_names ]
IP.1 = 192.168.31.104

[ v3_ext ]
authorityKeyIdentifier=keyid,issuer:always
basicConstraints=CA:FALSE
keyUsage=keyEncipherment,dataEncipherment
extendedKeyUsage=serverAuth,clientAuth
subjectAltName=@alt_names
```

生成 CSR:
```s
openssl req -new -key kube-etcd-192-168-31-104-key.pem -out kube-etcd-192-168-31-104.csr -config csr.conf
```

c. 生成证书
```s
sudo openssl x509 -req -in kube-etcd-192-168-31-104.csr -CA /etc/kubernetes/ssl/kube-ca.pem -CAkey /etc/kubernetes/ssl/kube-ca-key.pem \
-CAcreateserial -out kube-etcd-192-168-31-104.pem -days 10000 \
-extensions v3_ext -extfile csr.conf
```

现在，我们有了下面几个文件，主要的就是证书以及密钥：
```s
[decent@master1 learner]$ ls
csr.conf  kube-etcd-192-168-31-104.csr  kube-etcd-192-168-31-104-key.pem  kube-etcd-192-168-31-104.pem
```
下面需要将证书拷贝到 learner 目标节点，然后启动 etcd。

#### 添加 learner 节点
参考官方文档 [How to Add and Remove Members](https://etcd.io/docs/v3.5/tutorials/how-to-deal-with-membership/) 在 master1 节点执行添加操作，命令为：
```s
sudo etcdctl --endpoints=https://192.168.31.101:2379 \
    --cert=/etc/kubernetes/ssl/kube-etcd-192-168-31-101.pem \
    --key=/etc/kubernetes/ssl/kube-etcd-192-168-31-101-key.pem \
    --cacert=/etc/kubernetes/ssl/kube-ca.pem \
	member add --learner learner1 --peer-urls=https://192.168.31.104:2380
```

然后需要在 192.168.31.104 节点，也就是 node1 节点执行启动 etcd 命令，【注意，在实际 etcd learner 部署中，learner 也要以容器的形式部署】
```s
CLUSTER=etcd-master1=https://192.168.31.101:2380,etcd-master2=https://192.168.31.102:2380,etcd-master3=https://192.168.31.103:2380,learner1=https://192.168.31.104:2380

sudo etcd --data-dir=/var/lib/etcd --name=learner1 \
	--initial-advertise-peer-urls=https://192.168.31.104:2380 \
	--listen-peer-urls=https://192.168.31.104:2380 \
	--advertise-client-urls=https://192.168.31.104:2379 \
	--listen-client-urls=https://192.168.31.104:2379 \
	--initial-cluster=${CLUSTER} \
	--initial-cluster-state=existing \
	--peer-trusted-ca-file=/etc/kubernetes/ssl/kube-ca.pem \
    --peer-key-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-104-key.pem \
    --enable-v2=true \
    --initial-cluster-token=etcd-cluster-1 \
    --cert-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-104.pem \
    --peer-client-cert-auth=true \
    --client-cert-auth=true \
    --election-timeout=5000 \
    --trusted-ca-file=/etc/kubernetes/ssl/kube-ca.pem \
    --key-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-104-key.pem \
    --peer-cert-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-104.pem \
    --heartbeat-interval=500 
```

查看集群列表，看上去是正常的。
```s
[decent@master1 etcd]$ sudo etcdctl --endpoints=https://192.168.31.101:2379,https://192.168.31.102:2379,https://192.168.31.103:2379,https://192.168.31.104:2379 --cert=/etc/kubernetes/ssl/kube-etcd-192-168-31-101.pem --key=/etc/kubernetes/ssl/kube-etcd-192-168-31-101-key.pem --cacert=/etc/kubernetes/ssl/kube-ca.pem endpoint status --write-out=table
+-----------------------------+------------------+---------+---------+-----------+------------+-----------+------------+--------------------+--------+
|          ENDPOINT           |        ID        | VERSION | DB SIZE | IS LEADER | IS LEARNER | RAFT TERM | RAFT INDEX | RAFT APPLIED INDEX | ERRORS |
+-----------------------------+------------------+---------+---------+-----------+------------+-----------+------------+--------------------+--------+
| https://192.168.31.101:2379 | 79d5043c5c6f48d3 |  3.4.14 |  3.8 MB |      true |      false |         2 |      65451 |              65451 |        |
| https://192.168.31.102:2379 | affd5634331a4b9f |  3.4.14 |  3.8 MB |     false |      false |         2 |      65451 |              65451 |        |
| https://192.168.31.103:2379 | 178e111d1072acde |  3.4.14 |  3.8 MB |     false |      false |         2 |      65451 |              65451 |        |
| https://192.168.31.104:2379 | 880f846b9d56bd7d |   3.5.4 |  3.8 MB |     false |       true |         2 |      65451 |              65451 |        |
+-----------------------------+------------------+---------+---------+-----------+------------+-----------+------------+--------------------+--------+
```

#### 部署应用
部署一些应用，我们以部署一个 ReplicaSet 为例，使用下面 yaml 部署。
```yml
apiVersion: apps/v1
kind: ReplicaSet
metadata:
  name: hello-world
  labels:
    app: hello-world
spec:
  selector:
    matchLabels:
      app: hello-world
  replicas: 2
  template:
    metadata:
      labels:
        app: hello-world
    spec:
      containers:
        - name: hello-world
          image: jtblin/node-hello:latest
          imagePullPolicy: IfNotPresent
          ports:
            - containerPort: 8080
              protocol: TCP
```

查看 pod 状态
```s
[decent@master1 resources]$ kubectl get pods -o wide
NAME                READY   STATUS    RESTARTS   AGE     IP          NODE      NOMINATED NODE   READINESS GATES
hello-world-8f6hq   1/1     Running   0          2m52s   10.42.1.8   master2   <none>           <none>
hello-world-kvbjk   1/1     Running   0          2m52s   10.42.2.8   master1   <none>           <none>
```

#### 模拟故障
这里要模拟的故障是三个 Master 节点全挂了，然后通过 learner 拉起一个全新的 etcd 集群，然后再通过这个 etcd 集群恢复出 kubernetes 集群。预期的情况为：

a. 三台 Master master1/master2/master3 断电，直接将三台机器关机。
b. 通过 learner 单独拉起一个 etcd 集群，因为我们在 node1 部署了一个 learner，所以在 node1 单独拉起 kubernetes 集群。
c. 通过 rke 恢复出一个 kubernetes 集群，恢复出的集群有 node1 一个 Master 节点，有node2 一个 worker 节点。

> 我们需要做一些准备工作，因为 node1 要做新的部署机（也就是运行 rke up 命令的机器），所以需要：1.把 cluster.yaml 拷贝到 node1（需要提前拷贝，毕竟之前的机器断电了可能就无法登录了）；2. 配置 node1 可以免密登录到新集群的所有节点。3.安装 rke binary。

我们先来看 b，rancher 的文档提供了一个查看容器运行命令的镜像，其命令如下，就是查看 etcd 容器的启动命令，我们事先在 master1 运行下面命令，得到 etcd 的启动命令，并稍微修改一下。
```s
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock assaflavie/runlike etcd
```

因为我们要通过 learner 的节点启动一个 etcd 集群，需要修改的地方有：1）IP 地址。2）数据目录（配置好了也不用改）。3）证书文件。4）添加参数 --force-new-cluster。修改后的命令为：
```s
docker run --name=etcd --hostname=master1 --env=ETCDCTL_API=3 \
  --env=ETCDCTL_CACERT=/etc/kubernetes/ssl/kube-ca.pem \
  --env=ETCDCTL_CERT=/etc/kubernetes/ssl/kube-etcd-192-168-31-104.pem \
  --env=ETCDCTL_KEY=/etc/kubernetes/ssl/kube-etcd-192-168-31-104-key.pem \
  --env=ETCDCTL_ENDPOINTS=https://127.0.0.1:2379 \
  --env=ETCD_UNSUPPORTED_ARCH=x86_64 \
  --env=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  --volume=/var/lib/etcd:/var/lib/rancher/etcd/:z \
  --volume=/etc/kubernetes:/etc/kubernetes:z \
  --network=host --restart=always --label='io.rancher.rke.container.name=etcd' --runtime=runc --detach=true \
  rancher/coreos-etcd:v3.4.14-rancher1 /usr/local/bin/etcd --data-dir=/var/lib/rancher/etcd/ \
  --listen-peer-urls=https://0.0.0.0:2380 \
  --initial-cluster=etcd-learner1=https://192.168.31.104:2380 \
  --peer-trusted-ca-file=/etc/kubernetes/ssl/kube-ca.pem \
  --name=etcd-learner1 \
  --initial-cluster-state=new \
  --peer-key-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-104-key.pem \
  --advertise-client-urls=https://192.168.31.104:2379 \
  --enable-v2=true --initial-cluster-token=etcd-learner1 \
  --listen-client-urls=https://0.0.0.0:2379 \
  --initial-advertise-peer-urls=https://192.168.31.104:2380 \
  --cert-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-101.pem \
  --peer-client-cert-auth=true \
  --client-cert-auth=true --election-timeout=5000 \
  --trusted-ca-file=/etc/kubernetes/ssl/kube-ca.pem \
  --key-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-104-key.pem \
  --peer-cert-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-104.pem \
  --heartbeat-interval=500 \
  --force-new-cluster
```

现在将三台机器断电模拟故障。【done】

将 learner etcd 停掉，并使用上面的命令启动一个 etcd 容器。【在通过 learner 启动一个新集群时，etcd panic 了从日志信息来看，应该是不能通过 learner 来恢复一个新集群，这种情况下，所有的 voters 都被删除了，learner 不是 voter】
```s
panic: removed all voters

goroutine 135 [running]:
go.etcd.io/etcd/raft.(*raft).applyConfChange(0xc0000f2500, 0x0, 0xc0001db920, 0x1, 0x1, 0x0, 0x0, 0x0, 0x0, 0x0, ...)
        /tmp/etcd-release-3.4.14/etcd/release/etcd/raft/raft.go:1514 +0x195
go.etcd.io/etcd/raft.(*node).run(0xc00007f4a0)
        /tmp/etcd-release-3.4.14/etcd/release/etcd/raft/node.go:356 +0x78d
created by go.etcd.io/etcd/raft.RestartNode
        /tmp/etcd-release-3.4.14/etcd/release/etcd/raft/node.go:240 +0x33c
```
由此可以看出，是无法通过 `--force-new-cluster` 将 learner 节点转换为单 etcd 节点集群的。

#### 回到起点，三个 etcd 加一个 learner
现在又回到原点，添加一个 learner 节点，并从 learner 恢复出一个 etcd 集群，然后通过修改 kubu-apiserver 参数 `etcd-servers` 重新拉起一个集群。
目前 K8s 集群时这样的：
```s
[decent@master3 rke]$ kubectl get nodes
NAME      STATUS   ROLES                      AGE   VERSION
master1   Ready    controlplane,etcd,worker   11h   v1.20.5
master2   Ready    controlplane,etcd,worker   11h   v1.20.5
master3   Ready    controlplane,worker        36h   v1.20.5
node1     Ready    etcd,worker                36h   v1.20.5
node2     Ready    worker                     36h   v1.20.5
```
假设目前情况是这样的，分两个机房，A机房：master1，master2，node1；B机房：master3，node2。所有三个 etcd 节点都部署在 A 机房了，现在要在 B机房的 Master3 上部署一个 learner 节点。目前集群的etcd 集群时这样的
```s
[decent@master1 ~]$ sudo etcdctl --endpoints=https://192.168.31.101:2379,https://192.168.31.102:2379,https://192.168.31.104:2379 \
 member list --write-out=table
+------------------+---------+-----------------+-----------------------------+-----------------------------+------------+
|        ID        | STATUS  |      NAME       |         PEER ADDRS          |        CLIENT ADDRS         | IS LEARNER |
+------------------+---------+-----------------+-----------------------------+-----------------------------+------------+
| 26759ee2a67ff55a | started | learner-master3 | https://192.168.31.103:2380 | https://192.168.31.103:2379 |       true |
| 6336250cfb6726d7 | started |    etcd-master2 | https://192.168.31.102:2380 | https://192.168.31.102:2379 |      false |
| d86f3e7f7e03d5f5 | started |      etcd-node1 | https://192.168.31.104:2380 | https://192.168.31.104:2379 |      false |
| e3daf1084594a323 | started |    etcd-master1 | https://192.168.31.101:2380 | https://192.168.31.101:2379 |      false |
+------------------+---------+-----------------+-----------------------------+-----------------------------+------------+
```

现在模拟故障，关掉 master1/master2/node1 节点的电源。然后停掉 master3 节点的的 kube-apiserver。
```s
docker stop kube-apiserver
```
停掉 etcd (直接 kill 掉 learner etcd 的进程)

尝试在 cluster.yaml 中移除master1、master2、node1，期望 rke 能帮我们移除节点，并在 master3 帮我们起一个 etcd 实例。操作结果如下，从 rke 的这个错误可以看出，rke 必须指定一个节点为 etcd 节点。
```s
[decent@master3 rke]$ rke up --config cluster.yml
INFO[0000] Running RKE version: v1.2.7                  
INFO[0000] Initiating Kubernetes cluster                
INFO[0000] [certificates] GenerateServingCertificate is disabled, checking if there are unused kubelet certificates 
INFO[0000] [certificates] Generating Kubernetes API server certificates 
INFO[0000] [certificates] Generating admin certificates and kubeconfig 
INFO[0000] [certificates] Deleting unused certificate: kube-etcd-192-168-31-104 
INFO[0000] [certificates] Deleting unused certificate: kube-etcd-192-168-31-101 
INFO[0000] [certificates] Deleting unused certificate: kube-etcd-192-168-31-102 
INFO[0000] Successfully Deployed state file at [./cluster.rkestate] 
INFO[0000] Building Kubernetes cluster                  
INFO[0000] [dialer] Setup tunnel for host [192.168.31.105] 
INFO[0000] [dialer] Setup tunnel for host [192.168.31.103] 
FATA[0000] Cluster must have at least one etcd plane host: please specify one or more etcd in cluster config 
```
在指定 master3 为 etcd 节点之后，又报了下面的错误，看了下代码出现这个错误的原因是：原 K8s 集群的 etcd 集群与当前 K8s 集群的 etcd 集群没有交集。对比我们的情况，原来的 Etcd 集群都在 A 机房，想在 B 机房拉起一个 Etcd 集群，因为两个 Etcd 集群之间没有交集，所以报了下面的错误。所以根据 rancher 的设计思路，原集群跟现有集群是要有交集的。
```s
INFO[0011] [remove/file-deployer] Successfully removed container on host [192.168.31.103] 
INFO[0011] [/etc/kubernetes/audit-policy.yaml] Successfully deployed audit policy file to Cluster control nodes 
INFO[0011] [reconcile] Reconciling cluster state        
FATA[0011] Failed to reconcile etcd plane: Etcd plane nodes are replaced. Stopping provisioning. Please restore your cluster from backup.
```

learner 计划再次失败了

#### learner 拉起外部 etcd
但是 rke 支持外部 Etcd，只能走这个思路了，rke 关于外部 Etcd 的文档为[External etcd](https://rancher.com/docs/rke/latest/en/config-options/services/external-etcd/)。这个时候需要注意，我们启动的外部 Etcd 集群命令如下，将所有的 `ssl` 路径改成了 `ssl_old` 给 rke 让路。
```s
etcd  --name=new-cluster \
	--initial-advertise-peer-urls=https://192.168.31.103:2380 \
	--listen-peer-urls=https://192.168.31.103:2380 \
	--advertise-client-urls=https://192.168.31.103:2379 \
	--listen-client-urls=https://192.168.31.103:2379 \
	--initial-cluster=${CLUSTER} \
	--initial-cluster-state=new \
	--peer-trusted-ca-file=/etc/kubernetes/ssl_old/kube-ca.pem \
    --peer-key-file=/etc/kubernetes/ssl_old/kube-etcd-192-168-31-103-key.pem \
    --enable-v2=true \
    --initial-cluster-token=etcd-cluster-1 \
    --cert-file=/etc/kubernetes/ssl_old/kube-etcd-192-168-31-103.pem \
    --peer-client-cert-auth=true \
    --client-cert-auth=true \
    --election-timeout=5000 \
    --trusted-ca-file=/etc/kubernetes/ssl_old/kube-ca.pem \
    --key-file=/etc/kubernetes/ssl_old/kube-etcd-192-168-31-103-key.pem \
    --peer-cert-file=/etc/kubernetes/ssl_old/kube-etcd-192-168-31-103.pem \
    --heartbeat-interval=500 
```

当我们部署了外部 Etcd 之后，还是报上面的错误，即 `FATA[0011] Failed to reconcile etcd plane: Etcd plane nodes are replaced. Stopping provisioning. Please restore your cluster from backup`，仔细一想，也合理，看上去应该是如果要使用外部 Etcd 集群，那么就要一开始使用外部 Etcd。

那我们索性用这个外部的 Etcd 重新拉一个 K8s 集群出来，把原来的K8s 的配置数据都删掉（证书、配置文件等）。**额外强调一下，原来的证书都要保存，比如将 ssl 路径重新命名为 ssl_old，因为新拉起的 etcd 集群还要用这些证书**。

经过一番折腾，集群终于起来了，目前的集群状态为，有三个节点 NotReady，就是机房A崩掉的那三台。
```s
[decent@master3 rke]$ kubectl get nodes
NAME      STATUS     ROLES                      AGE    VERSION
master1   NotReady   controlplane,etcd,worker   2d     v1.20.5
master2   NotReady   controlplane,etcd,worker   2d     v1.20.5
master3   Ready      controlplane,worker        3d1h   v1.20.5
node1     NotReady   etcd,worker                3d1h   v1.20.5
node2     Ready      worker                     50m    v1.20.5
```
然后查看 pod，也是正常的，所以我们单节点通过 learner 拉起的 etcd 集群也是正常的。
```s
[decent@master3 rke]$ kubectl get pods -o wide
NAME                READY   STATUS    RESTARTS   AGE    IP           NODE      NOMINATED NODE   READINESS GATES
hello-world-kxtms   1/1     Running   0          22h    10.42.0.19   master3   <none>           <none>
hello-world-tfch9   1/1     Running   0          2d2h   10.42.0.17   master3   <none>           <none>
pods-simple-pod     1/1     Running   14         2d2h   10.42.4.8    node1     <none>           <none>
```

补充一下，在 rke cluster.yaml 配置外部 etcd 的方式，需要配置五个字段，如下，其中证书，就是 rke 帮我们生成的 /etc/kubernetes/ssl 目录中的内容。path 字段是 etcd prefix，根据 apiserver 的参数，是`/registry`
```yml
services:
  etcd:
    image: ""
    extra_args: {}
    extra_binds: []
    extra_binds: []
    extra_env: []
    win_extra_args: {}
    win_extra_binds: []
    win_extra_env: []
    external_urls: ["https://192.168.31.103:2379"]
    ca_cert: |-
      -----BEGIN CERTIFICATE-----
      MIICwjCCAaqgAwIBAgIBADANBgkqhkiG9w0BAQsFADASMRAwDgYDVQQDEwdrdWJl
      LWNhMB4XDTIyMDUzMDA4MjcwNloXDTMyMDUyNzA4MjcwNlowEjEQMA4GA1UEAxMH
      a3ViZS1jYTCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBALoHRvuVO3jN
      GAUWKUZC+D25C1e0mgHtbe7uQvny955lpV1yjDMaCL/VameYcNRgMBlvveYkADwZ
      7OF4URRQ3I6Qwr8gz/jxnBB42vYr8ILymRFzdzyCWFZoK2RFPhldtNPda6GVZrLz
      AdsVPavL8IxAHRVtfeWZgivyfqdDjWRrtgH7odbuwYBrNq0cBVu51Jqo9CxUs9sr
      JttJibve1oKrZ39ETUBx7jSrsnkelMfms50gVBolPT1em/Yk6Tm4sFGRJC+5vQoC
      NnQl0haIkNrojaLnTUBzcOObo3/TAcZqJ6O3F4kHF097LqVfLTzjAsa9nUQYVnz3
      iR6Nf2pFmokCAwEAAaMjMCEwDgYDVR0PAQH/BAQDAgKkMA8GA1UdEwEB/wQFMAMB
      Af8wDQYJKoZIhvcNAQELBQADggEBAD6M67xgWdWxsVUWUUTEcCgu0DCoFwqW2q3q
      PWuKXTJMCJKBKc41uSso3PLHugkwhB4P3f0lMH41M+BGS+r6453HuLDEoZvmK10i
      4XirQAGIqzFEM3pwgZrnmbnW8lNdrgRwteJ7x13nAi86xy7BfNgJCA9SA4W6+ad1
      JBRr0GwyQ+pg0t0qNmZ7t4nY4cQydT42BdKF/qxZiSpyvWT0VSZorRnaKlSOouuy
      QeL777fLAlIRSkM13Uj0ecuX2FWhq23EI4mkJOm5LA1Ce9b1ynnTJNeW7MW9AGYh
      mJssMO7MMsTsmVVdCmj9UOTf4LMNBeHyprJ/A9yWKEI8Fma1TaE=
      -----END CERTIFICATE-----
    cert: |-
      -----BEGIN CERTIFICATE-----
      MIIDbTCCAlWgAwIBAgIIfv3rks6DGHUwDQYJKoZIhvcNAQELBQAwEjEQMA4GA1UE
      AxMHa3ViZS1jYTAeFw0yMjA1MzAwODI3MDZaFw0zMjA1MjkwNzM4NDFaMBQxEjAQ
      BgNVBAMTCWt1YmUtZXRjZDCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEB
      AMnw79nl9+FlxzdQ7uOPagGt8pvhud8hfKAW9UeKZwXCuW615DgNCRCwk4v+zF+Y
      PGtsmPd2xodeku4+y2x/wwU1pkaxmM98Jm8sphvpwCPZiWkhsVwmA0DHmyC4LRPK
      FW0zj/uCYnfK9YsPGFGo7qSm+5b0m9cGJ7xJhmopKoDoQu2pKgUE2tUmgQH2D1ud
      kl/enVoUrqQPgtVVTVHFe1Njxzev7gWT9wwF2x90R3WSG0C7mRj3zOi/iU8oZrSt
      vnQuBJVxLV1gq1zfjhoVBMxJ7kB5wr0Ea9nHNtFxWszmGRSfDB/J2FqhI4L8M1eM
      Lfo0BLJPL2Pd3VOkQiZKsi0CAwEAAaOBxDCBwTAOBgNVHQ8BAf8EBAMCBaAwHQYD
      VR0lBBYwFAYIKwYBBQUHAwIGCCsGAQUFBwMBMIGPBgNVHREEgYcwgYSCB21hc3Rl
      cjOCCWxvY2FsaG9zdIIKa3ViZXJuZXRlc4ISa3ViZXJuZXRlcy5kZWZhdWx0ghZr
      dWJlcm5ldGVzLmRlZmF1bHQuc3ZjgiRrdWJlcm5ldGVzLmRlZmF1bHQuc3ZjLmNs
      dXN0ZXIubG9jYWyHBMCoH2eHBH8AAAGHBAorAAEwDQYJKoZIhvcNAQELBQADggEB
      AFC5kY6KutNcnSekODC9YsP/4qJRLxXBsloy4kJ3UUGQ8PeO4/QHyiYlEOP5Vf5K
      EHfryF16QKpCUROnX6IVyXd+Ep6GxMHniXBy/ZJVEwoHVYciAyp9IPxUYQ54oCKp
      DNoh9knSExF3L7aOGbZtHXVkO6D01mkhJmn6Hf4CszSpQ/sOeGRPNpZC5BRu3mfQ
      ojQP56moeni29+XjJ6cuIkwLzjmGzSPU3t2kQrAGzx89o4N4JEBKjS6EVkJLc03D
      nSB2IFN8MeDn7OKnU6bbAYY8WyUzUe4aM3G7Cbffgq+Oc9iAjQH3R1JgB86S8wd+
      B7x4VQxl0ODyZszfEWjl1gw=
      -----END CERTIFICATE-----
    key: |-
      -----BEGIN RSA PRIVATE KEY-----
      MIIEowIBAAKCAQEAyfDv2eX34WXHN1Du449qAa3ym+G53yF8oBb1R4pnBcK5brXk
      OA0JELCTi/7MX5g8a2yY93bGh16S7j7LbH/DBTWmRrGYz3wmbyymG+nAI9mJaSGx
      XCYDQMebILgtE8oVbTOP+4Jid8r1iw8YUajupKb7lvSb1wYnvEmGaikqgOhC7akq
      BQTa1SaBAfYPW52SX96dWhSupA+C1VVNUcV7U2PHN6/uBZP3DAXbH3RHdZIbQLuZ
      GPfM6L+JTyhmtK2+dC4ElXEtXWCrXN+OGhUEzEnuQHnCvQRr2cc20XFazOYZFJ8M
      H8nYWqEjgvwzV4wt+jQEsk8vY93dU6RCJkqyLQIDAQABAoIBACpiOkUWgjc5gF14
      zBrQz+P4WVIkRzmwspJ7HxHb15Ga9AZrgLHO8pchKGaanNR3hD7btSNDN5nT3KzK
      WzGzgwAF6Zcu0S7DFOICkf6Lyfr8Pl3lZ286vzYKbuGoJjXgFS0tREv6aqZP38dG
      7Mi+1w+RyH4/arHvpclb+S0w5K3XMjsrdCARGJjIRuXKKoWBI9Puc3zl6/Lrn51u
      Lsf9eNt5htsjcpRTXENG/nwJ1AI+CtGLoTLGkkknwnm2t1n/4qNWvn+5r3lmgarl
      AhZRJCS5mK6BAtPmbb+DXyopSxNqeDMm0Xf3M+8V/ekF/TG/03z7EG0j82n3cGeo
      USys8bUCgYEA5KI35XPCR0ptSWon6Suz68voooXRzJWmSXjfqEe8ot7aw0bafizj
      HnMeww3z5kn6o5ejxP3swEMsHu9Hc9bKXDl60Z85kD/D1yAuCql27ndGmBKw+E/n
      FxgcZer1wms/OzBjTlvUuJ230sR+hlxssZJKKP00xXDvn5m/lpDIgLMCgYEA4hzO
      4LUewXCdMb9tg05iyUSwjSoInREUlodmXFvAp5nrHCkSYDxWYjeBIGMWjd+jNcim
      UoG+x1fMQvvlBfxoEju3bUf+C3+VXVcEF7YD5taXLZql8akmdQxjzQTsK9hYY7u3
      SnjGBTYXeMbnuDCcn+uKsMRgShZ2afazsCmdsZ8CgYB0EBlaBJKqSBEEhLwv9PyI
      BeJZpp8jQRDCGXdIYOpUr9bT7ML77GN7UKtcD1gyHnn60/7SAKlPzIm4RnW6S148
      xP0hLrg1Dvmm2nIk/XQfiDMw/cQSudUw9w9reYQ6puDZdi3jWGC7O21WtGMaaA7R
      cdbtyeQhGry6A32rvGHcWwKBgQC6fNATfM5U9LBxa1TDS08meMS0aMqZ4JB+ZkYC
      PppyoPvMSgOh46HLd8PEFnVvpddScJ7cxa23c65AQMjvWvHqt93c/9eDXEKwrSfu
      9mvZY3tkXXwoCD5zozhcy4aN0u1ztErD3UBU4/wP3N0YiN9k9jt49z4DXOtlEde5
      n1k0OQKBgFieFy4FFoZ5t7u8GaNQhodvjuuXNISuRkMWUG9s72iAH/+w3aFiKk6p
      rqsIi3nUhgbjVp3aO3naQKruPq3ZqcbEcB0L3oPabCTOJ55TeTDMdQKUiP20p2V+
      IXapPEAQZVcovMNWyFB59CDbl59gDX8Un0pm3mRtQigvi8kqJq0U
      -----END RSA PRIVATE KEY-----   
    path: "/registry"
    uid: 0
    gid: 0
    snapshot: null
    retention: ""
    creation: ""
    backup_config: null
```

### rke 可以直接移除两个 master 节点吗？
如果我们集群有三个 etcd 节点，两个节点挂了，那么可以直接移除 `cluster.yaml` 中的两个 etcd 节点来恢复集群吗？`rke up`日志如下：
```s
time="2022-06-12T14:34:48+08:00" level=info msg="[hosts] host [192.168.31.102] has another role, skipping delete from kubernetes cluster"
time="2022-06-12T14:34:48+08:00" level=info msg="[dialer] Setup tunnel for host [192.168.31.102]"
time="2022-06-12T14:34:52+08:00" level=info msg="[dialer] Setup tunnel for host [192.168.31.102]"
time="2022-06-12T14:34:55+08:00" level=info msg="[dialer] Setup tunnel for host [192.168.31.102]"
time="2022-06-12T14:34:58+08:00" level=warning msg="[reconcile] Couldn't clean up worker node [192.168.31.102]: Not able to reach the host: Can't retrieve Docker Info: error during connect: Get \"http://%2Fvar%2Frun%2Fdocker.sock/v1.24/info\": Failed to dial ssh using address [192.168.31.102:22]: dial tcp 192.168.31.102:22: connect: no route to host"
time="2022-06-12T14:34:58+08:00" level=info msg="[hosts] Cordoning host [192.168.31.101]"
time="2022-06-12T14:37:23+08:00" level=fatal msg="Failed to delete controlplane node [192.168.31.101] from cluster: etcdserver: request timed out"
```
> 2022.06.24 补充。尽管上面报了一个 `fatal` 错误，但是其实两个 master 节点是正常被移除的，但是这些操作需要一些前置条件：需要首先将剩下那个 master 节点 `--force-new-cluster`

另外，在挂掉两个 etcd 节点的 K8s 集群中，还可以读吗？我们执行 `kubectl get nodes` 时输出如下，（kubectl get nodes 默认是线性读？）
```s
[decent@master3 rke_test]$ kubectl get nodes
Error from server: etcdserver: request timed out
```

#### 其他问题
##### kubelet 不断重启
拉起的过程中遇到了 kubelet 不断重启的问题，但是也没看出重启的原因，就是一堆 goroutine 信息，像是 panic 了。
```s
F0601 11:40:13.749942  107652 kubelet.go:1372] Failed to start ContainerManager failed to build map of initial containers from runtime: no PodsandBox found with Id '42b055b259dfdf19524ca133bc2a025e5ae754ab6ad368bb5c00269a3d0465ab'
```
参考[Kubelet goes to a cyclic restart loop when inconsistent container list received from runtimeservice #21](https://github.com/kubernetes/kubelet/issues/21)，解决方式为：
```s
Find broken container (docker ps -a --filter "label=io.kubernetes.sandbox.id=894f35dca3eda57adef28b69acd0607efdeb34e8814e87e196bc163305576028" <--- id from error message) and remove it using docker rm ID, then restart kubelet.
```
差点就重新买台新电脑了（其实把所以 stop 的容器 rm 也能解决问题，后面命令是删除所有容器 `for i in $(docker ps -a | awk '{print $1}'); do docker stop ${i}; docker rm ${i}; done`）。

##### ntp 不同步，导致 kubelet 无法启动
之后再次重建集群，遇到了下面的问题
```s
I0601 04:49:01.487830    5373 csi_plugin.go:1039] Failed to contact API server when waiting for CSINode publishing: Get "https://127.0.0.1:6443/apis/storage.k8s.io/v1/csinodes/node2": x509: certificate has expired or is not yet valid: current time 2022-06-01T04:49:01Z is before 2022-06-01T12:53:50Z
```

经过使用 ntp 同步时间后解决
```s
#安装ntpdate
sudo yum -y install ntpdate
#同步时间
sudo ntpdate -u  pool.ntp.org
#设置时区
sudo timedatectl set-timezone Asia/Shanghai
date
```