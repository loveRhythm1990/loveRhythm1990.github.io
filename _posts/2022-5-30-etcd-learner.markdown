---
layout:     post
title:      "Etcd 灾备 force new cluster 以及 learner - 操作记录"
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
[decent@master1 ~]$ sudo etcdctl --endpoints=https://192.168.31.101:2379 \
 --cert=/etc/kubernetes/ssl/kube-etcd-192-168-31-101.pem \
 --key=/etc/kubernetes/ssl/kube-etcd-192-168-31-101-key.pem \
 --cacert=/etc/kubernetes/ssl/kube-ca.pem \
 member list --write-out=table
+------------------+---------+--------------+-----------------------------+-----------------------------+------------+
|        ID        | STATUS  |     NAME     |         PEER ADDRS          |        CLIENT ADDRS         | IS LEARNER |
+------------------+---------+--------------+-----------------------------+-----------------------------+------------+
| 178e111d1072acde | started | etcd-master3 | https://192.168.31.103:2380 | https://192.168.31.103:2379 |      false |
| 79d5043c5c6f48d3 | started | etcd-master1 | https://192.168.31.101:2380 | https://192.168.31.101:2379 |      false |
| affd5634331a4b9f | started | etcd-master2 | https://192.168.31.102:2380 | https://192.168.31.102:2379 |      false |
+------------------+---------+--------------+-----------------------------+-----------------------------+------------+
```

`member list` 命令输出的信息有限，我们通过`endpoint status` 命令来查看，`--endpoints` 指定三个节点，证书使用一个节点就可以了。
```s
[decent@master1 ~]$ sudo etcdctl --endpoints=https://192.168.31.101:2379,https://192.168.31.102:2379,https://192.168.31.103:2379 --cert=/etc/kubernetes/ssl/kube-etcd-192-168-31-101.pem --key=/etc/kubernetes/ssl/kube-etcd-192-168-31-101-key.pem --cacert=/etc/kubernetes/ssl/kube-ca.pem endpoint status --write-out=table
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

pod `hello-world-8f6hq` 的元数据
```yml
apiVersion: v1
kind: Pod
metadata:
  creationTimestamp: "2022-05-30T17:23:26Z"
  generateName: hello-world-
  labels:
    app: hello-world
  name: hello-world-8f6hq
  namespace: default
  ownerReferences:
  - apiVersion: apps/v1
    blockOwnerDeletion: true
    controller: true
    kind: ReplicaSet
    name: hello-world
    uid: f49ab8f6-8a75-4339-ade0-5064488a8908
  resourceVersion: "62856"
  uid: 3b70669e-cabf-4875-8093-fce3a8d7f5bd
spec:

status:
  conditions:
  - lastProbeTime: null
    lastTransitionTime: "2022-05-30T17:23:36Z"
    status: "True"
    type: Initialized
  - lastProbeTime: null
    lastTransitionTime: "2022-05-30T17:23:37Z"
    status: "True"
    type: Ready
  - lastProbeTime: null
    lastTransitionTime: "2022-05-30T17:23:37Z"
    status: "True"
    type: ContainersReady
  - lastProbeTime: null
    lastTransitionTime: "2022-05-30T17:23:35Z"
    status: "True"
    type: PodScheduled
  containerStatuses:
  - containerID: docker://b93bcb3f4bd31baa3440d237339c1af9051ec11b97b13ddab4e331fe4690acc1
    image: jtblin/node-hello:latest
    imageID: docker-pullable://jtblin/node-hello@sha256:d37d8ffa48da75842675c30487131cf3122c1f0f44907d618b16c3b53d73babb
    lastState: {}
    name: hello-world
    ready: true
    restartCount: 0
    started: true
    state:
      running:
        startedAt: "2022-05-30T17:23:36Z"
  hostIP: 192.168.31.102
  phase: Running
  podIP: 10.42.1.8
  podIPs:
  - ip: 10.42.1.8
  qosClass: BestEffort
  startTime: "2022-05-30T17:23:36Z"
```
同时，也按照 rancher [官方文档](https://rancher.com/docs/rancher/v2.0-v2.4/en/installation/install-rancher-on-k8s/)部署了 rancher。

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

为了验证 `--force-new-cluster`，我们重新启动一个 master 机器，假设为 master3，并在上面 --force-new-cluster。通过 `assaflavie/runlike` 得到的 docker 运行的命令为【需要修改--initial-cluster参数，以及添加 --force-new-cluster】：
```s
docker run --name=etcd --hostname=master3 --env=ETCDCTL_API=3 --env=ETCDCTL_CACERT=/etc/kubernetes/ssl/kube-ca.pem --env=ETCDCTL_CERT=/etc/kubernetes/ssl/kube-etcd-192-168-31-103.pem --env=ETCDCTL_KEY=/etc/kubernetes/ssl/kube-etcd-192-168-31-103-key.pem --env=ETCDCTL_ENDPOINTS=https://127.0.0.1:2379 --env=ETCD_UNSUPPORTED_ARCH=x86_64 --env=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin --volume=/var/lib/etcd:/var/lib/rancher/etcd/:z --volume=/etc/kubernetes:/etc/kubernetes:z --network=host --restart=always --label='io.rancher.rke.container.name=etcd' --runtime=runc --detach=true rancher/coreos-etcd:v3.4.14-rancher1 /usr/local/bin/etcd --peer-cert-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-103.pem --advertise-client-urls=https://192.168.31.103:2379 --peer-client-cert-auth=true --client-cert-auth=true --initial-cluster-token=etcd-cluster-1 --listen-client-urls=https://0.0.0.0:2379 --initial-cluster=etcd-master3=https://192.168.31.103:2380 --initial-cluster-state=new --peer-trusted-ca-file=/etc/kubernetes/ssl/kube-ca.pem --key-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-103-key.pem --peer-key-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-103-key.pem --election-timeout=5000 --data-dir=/var/lib/rancher/etcd/ --name=etcd-master3 --listen-peer-urls=https://0.0.0.0:2380 --heartbeat-interval=500 --enable-v2=true --initial-advertise-peer-urls=https://192.168.31.103:2380 --trusted-ca-file=/etc/kubernetes/ssl/kube-ca.pem --cert-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-103.pem --force-new-cluster
```
将原来的 etcd 停掉，并备份一下：
```s
docker stop etcd
docker rename etcd etcd-old
```
通过 docker run 命令来启动新的 etcd 集群，并查看状态，显示集群是健康的。
```s
[decent@master3 ~]$ sudo etcdctl --endpoints=https://192.168.31.103:2379  --cert=/etc/kubernetes/ssl/kube-etcd-192-168-31-103.pem  --key=/etc/kubernetes/ssl/kube-etcd-192-168-31-103-key.pem  --cacert=/etc/kubernetes/ssl/kube-ca.pem  endpoint health
https://192.168.31.103:2379 is healthy: successfully committed proposal: took = 14.413366ms
```
这个时候我们在 master3 节点上查看 Kubernetes 集群状态，发现集群也是正常的，也能正常做变更。
```s
[decent@master3 ~]$ kubectl get pods -o wide
NAME                READY   STATUS        RESTARTS   AGE     IP          NODE      NOMINATED NODE   READINESS GATES
hello-world-8f6hq   1/1     Terminating   0          14h     10.42.1.8   master2   <none>           <none>
hello-world-kvbjk   1/1     Terminating   0          14h     10.42.2.8   master1   <none>           <none>
hello-world-tfch9   1/1     Running       0          9m27s   10.42.0.4   master3   <none>           <none>
hello-world-vwpwh   1/1     Running       0          9m37s   10.42.3.6   node2     <none>           <none>
[decent@master3 ~]$ kubectl delete pod hello-world-kvbjk --force
warning: Immediate deletion does not wait for confirmation that the running resource has been terminated. The resource may continue to run on the cluster indefinitely.
pod "hello-world-kvbjk" force deleted
[decent@master3 ~]$ kubectl get pods
NAME                READY   STATUS        RESTARTS   AGE
hello-world-8f6hq   1/1     Terminating   0          14h
hello-world-tfch9   1/1     Running       0          18m
hello-world-vwpwh   1/1     Running       0          18m
```

另外，测试下 pod 创建，通过下面命令，创建一个 pod 也是成功的。
```yml
apiVersion: v1
kind: Pod
metadata:
  name: pods-simple-pod
spec:
  containers:
    - command:
        - sleep
        - "3600"
      image: busybox
      name: pods-simple-container
```

查看 pod，被调度到了 pod1 上，是正常运行的。
```s
[decent@master3 ~]$ kubectl get pods pods-simple-pod -o wide
NAME              READY   STATUS    RESTARTS   AGE     IP          NODE    NOMINATED NODE   READINESS GATES
pods-simple-pod   1/1     Running   0          2m22s   10.42.4.8   node1   <none>           <none>
```

> 因为我是通过 rke 启动的 K8s 集群，需要将 kube_config_cluster.yml 文件拷贝到 masters 上，这样才能访问 kube-apiserver，同时 `kube_config_cluster.yml` 这个文件需要稍微修改一下，这么文件默认访问的是部署机，也就是 master1 上的 kube-apierver，我们需要修改下地址，将其改为 `server: "https://192.168.31.103:6443"`，这样才能访问 masters 上面的 apiserver。

上面本意是想通过 learner --force-new-cluster 感觉行不通，走了通过 master3 --force-new-cluster 的路子。这种情况下，集群应该是不需要变更的（不需要修改 rke 的 cluster.yaml 文件，同时 kube-apiserver 的apiserver 参数也不需要修改），只是原有的两个 master 节点 NotReady 了，理论上节点 NotReady 之后，上面的应用会发生驱逐，并重新调度到正常的节点上，这种情况下，做的工作还是比较少的，运维操作比较少。这个好好研究一下 rancher 的文档 [Recovering etcd without a Snapshotlink](https://rancher.com/docs/rancher/v2.0-v2.4/en/cluster-admin/restoring-etcd/)，感觉可行性还是比较高。
```s
[decent@master3 ~]$ kubectl get nodes
NAME      STATUS     ROLES                      AGE   VERSION
master1   NotReady   controlplane,etcd,worker   23h   v1.20.5
master2   NotReady   controlplane,etcd,worker   23h   v1.20.5
master3   Ready      controlplane,etcd,worker   23h   v1.20.5
node1     Ready      worker                     23h   v1.20.5
node2     Ready      worker                     23h   v1.20.5
```

> 现在我到了这样一个状况：集群中有两个 Master 节点挂掉了，我想把这两个 Master 节点删掉，然后重新加入 K8s 集群。删掉以及重新加入的操作都是通过编辑 rke 的 cluster.yaml 来做的。这里回顾一个 rke 的基础知识，在通过 `rke up --config cluster.yaml` 启动一个集群之后，会额外生成两个文件：`cluster.rkestate`、`kube_config_cluster.yml`，这两个文件在后面做集群变更的时候，是一定要存在的（跟 cluster.yml放在同一个目录）。我们删除 cluster.yml 中的两个节点来实现将其从 K8s 集群中删除。清理之后，再加回来。清理的动作有：1：删除所有容器。2）删除 /etc/kubernetes 目录。3）删除 /var/lib/etcd 目录。（其实还有一个操作是把 node1 上的 learner 删掉了）

一顿操作之后，集群恢复了：
```s
[decent@master3 rke]$ kubectl get nodes
NAME      STATUS   ROLES                      AGE   VERSION
master1   Ready    controlplane,etcd,worker   28m   v1.20.5
master2   Ready    controlplane,etcd,worker   28m   v1.20.5
master3   Ready    controlplane,etcd,worker   25h   v1.20.5
node1     Ready    worker                     25h   v1.20.5
node2     Ready    worker                     25h   v1.20.5
```

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
[decent@master1 ~]$ sudo etcdctl --endpoints=https://192.168.31.101:2379,https://192.168.31.102:2379,https://192.168.31.104:2379 --cert=/etc/kubernetes/ssl/kube-etcd-192-168-31-101.pem --key=/etc/kubernetes/ssl/kube-etcd-192-168-31-101-key.pem --cacert=/etc/kubernetes/ssl/kube-ca.pem member list --write-out=table
+------------------+---------+-----------------+-----------------------------+-----------------------------+------------+
|        ID        | STATUS  |      NAME       |         PEER ADDRS          |        CLIENT ADDRS         | IS LEARNER |
+------------------+---------+-----------------+-----------------------------+-----------------------------+------------+
| 26759ee2a67ff55a | started | learner-master3 | https://192.168.31.103:2380 | https://192.168.31.103:2379 |       true |
| 6336250cfb6726d7 | started |    etcd-master2 | https://192.168.31.102:2380 | https://192.168.31.102:2379 |      false |
| d86f3e7f7e03d5f5 | started |      etcd-node1 | https://192.168.31.104:2380 | https://192.168.31.104:2379 |      false |
| e3daf1084594a323 | started |    etcd-master1 | https://192.168.31.101:2380 | https://192.168.31.101:2379 |      false |
+------------------+---------+-----------------+-----------------------------+-----------------------------+------------+
```

现在模拟故障，关掉 master1/master2/node1 节点的电源。然后停掉 master3 节点的的 kube-apiserver
```s
docker stop kube-apiserver
```
停掉 etcd (直接 kill 掉 learner etcd 的进程)


从 rke 的这个错误可以看出，rke 必须指定一个节点为 etcd 节点。
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

拉起的过程中遇到了 kubelet 不断重启的问题，但是也没看出重启的原因，就是一堆 goroutine 信息，像是 panic 了。
```s
F0601 11:40:13.749942  107652 kubelet.go:1372] Failed to start ContainerManager failed to build map of initial containers from runtime: no PodsandBox found with Id '42b055b259dfdf19524ca133bc2a025e5ae754ab6ad368bb5c00269a3d0465ab'
```
参考[Kubelet goes to a cyclic restart loop when inconsistent container list received from runtimeservice #21](https://github.com/kubernetes/kubelet/issues/21)，解决方式为：
```s
Find broken container (docker ps -a --filter "label=io.kubernetes.sandbox.id=894f35dca3eda57adef28b69acd0607efdeb34e8814e87e196bc163305576028" <--- id from error message) and remove it using docker rm ID, then restart kubelet.
```
差点就重新买台新电脑了（其实把所以 stop 的容器 rm 也能解决问题，后面命令是删除所有容器 `for i in $(docker ps -a | awk '{print $1}'); do docker stop ${i}; docker rm ${i}; done`）。

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
如果我们集群有三个 etcd 节点，两个节点挂了，那么可以直接移除 `cluster.yaml` 中的两个 etcd 节点来恢复集群吗？操作了下，感觉是不可以的，`rke up`日志如下：
```s
time="2022-06-12T14:33:56+08:00" level=info msg="Running RKE version: v1.2.7"
time="2022-06-12T14:33:56+08:00" level=info msg="Initiating Kubernetes cluster"
time="2022-06-12T14:33:56+08:00" level=info msg="[certificates] GenerateServingCertificate is disabled, checking if there are unused kubelet certificates"
time="2022-06-12T14:33:56+08:00" level=info msg="[certificates] Generating admin certificates and kubeconfig"
time="2022-06-12T14:33:56+08:00" level=info msg="Successfully Deployed state file at [./cluster.rkestate]"
time="2022-06-12T14:33:56+08:00" level=info msg="Building Kubernetes cluster"
time="2022-06-12T14:33:56+08:00" level=info msg="[dialer] Setup tunnel for host [192.168.31.105]"
time="2022-06-12T14:33:56+08:00" level=info msg="[dialer] Setup tunnel for host [192.168.31.103]"
time="2022-06-12T14:33:56+08:00" level=info msg="[dialer] Setup tunnel for host [192.168.31.104]"
time="2022-06-12T14:33:59+08:00" level=warning msg="Failed to set up SSH tunneling for host [192.168.31.104]: Can't retrieve Docker Info: error during connect: Get \"http://%2Fvar%2Frun%2Fdocker.sock/v1.24/info\": Failed to dial ssh using address [192.168.31.104:22]: dial tcp 192.168.31.104:22: connect: no route to host"
time="2022-06-12T14:33:59+08:00" level=warning msg="Removing host [192.168.31.104] from node lists"
time="2022-06-12T14:33:59+08:00" level=info msg="[network] No hosts added existing cluster, skipping port check"
time="2022-06-12T14:33:59+08:00" level=info msg="[certificates] kube-apiserver certificate changed, force deploying certs"
time="2022-06-12T14:33:59+08:00" level=info msg="[certificates] Deploying kubernetes certificates to Cluster nodes"
time="2022-06-12T14:33:59+08:00" level=info msg="Checking if container [cert-deployer] is running on host [192.168.31.105], try #1"
time="2022-06-12T14:33:59+08:00" level=info msg="Checking if container [cert-deployer] is running on host [192.168.31.103], try #1"
time="2022-06-12T14:33:59+08:00" level=info msg="Image [rancher/rke-tools:v0.1.72] exists on host [192.168.31.105]"
time="2022-06-12T14:33:59+08:00" level=info msg="Image [rancher/rke-tools:v0.1.72] exists on host [192.168.31.103]"
time="2022-06-12T14:33:59+08:00" level=info msg="Starting container [cert-deployer] on host [192.168.31.105], try #1"
time="2022-06-12T14:33:59+08:00" level=info msg="Starting container [cert-deployer] on host [192.168.31.103], try #1"
time="2022-06-12T14:34:00+08:00" level=info msg="Checking if container [cert-deployer] is running on host [192.168.31.105], try #1"
time="2022-06-12T14:34:00+08:00" level=info msg="Checking if container [cert-deployer] is running on host [192.168.31.103], try #1"
time="2022-06-12T14:34:05+08:00" level=info msg="Checking if container [cert-deployer] is running on host [192.168.31.105], try #1"
time="2022-06-12T14:34:05+08:00" level=info msg="Removing container [cert-deployer] on host [192.168.31.105], try #1"
time="2022-06-12T14:34:05+08:00" level=info msg="Checking if container [cert-deployer] is running on host [192.168.31.103], try #1"
time="2022-06-12T14:34:05+08:00" level=info msg="Removing container [cert-deployer] on host [192.168.31.103], try #1"
time="2022-06-12T14:34:05+08:00" level=info msg="[reconcile] Rebuilding and updating local kube config"
time="2022-06-12T14:34:05+08:00" level=info msg="Successfully Deployed local admin kubeconfig at [./kube_config_cluster.yml]"
time="2022-06-12T14:34:05+08:00" level=info msg="[reconcile] host [192.168.31.103] is a control plane node with reachable Kubernetes API endpoint in the cluster"
time="2022-06-12T14:34:05+08:00" level=info msg="[certificates] Successfully deployed kubernetes certificates to Cluster nodes"
time="2022-06-12T14:34:05+08:00" level=info msg="[file-deploy] Deploying file [/etc/kubernetes/audit-policy.yaml] to node [192.168.31.103]"
time="2022-06-12T14:34:05+08:00" level=info msg="Image [rancher/rke-tools:v0.1.72] exists on host [192.168.31.103]"
time="2022-06-12T14:34:05+08:00" level=info msg="Starting container [file-deployer] on host [192.168.31.103], try #1"
time="2022-06-12T14:34:05+08:00" level=info msg="Successfully started [file-deployer] container on host [192.168.31.103]"
time="2022-06-12T14:34:05+08:00" level=info msg="Waiting for [file-deployer] container to exit on host [192.168.31.103]"
time="2022-06-12T14:34:05+08:00" level=info msg="Waiting for [file-deployer] container to exit on host [192.168.31.103]"
time="2022-06-12T14:34:05+08:00" level=info msg="Container [file-deployer] is still running on host [192.168.31.103]: stderr: [], stdout: []"
time="2022-06-12T14:34:06+08:00" level=info msg="Waiting for [file-deployer] container to exit on host [192.168.31.103]"
time="2022-06-12T14:34:06+08:00" level=info msg="Removing container [file-deployer] on host [192.168.31.103], try #1"
time="2022-06-12T14:34:06+08:00" level=info msg="[remove/file-deployer] Successfully removed container on host [192.168.31.103]"
time="2022-06-12T14:34:06+08:00" level=info msg="[/etc/kubernetes/audit-policy.yaml] Successfully deployed audit policy file to Cluster control nodes"
time="2022-06-12T14:34:06+08:00" level=info msg="[reconcile] Reconciling cluster state"
time="2022-06-12T14:34:06+08:00" level=info msg="[reconcile] Check etcd hosts to be deleted"
time="2022-06-12T14:34:06+08:00" level=info msg="[remove/etcd] Removing member [etcd-master1] from etcd cluster"
time="2022-06-12T14:34:21+08:00" level=warning msg="[reconcile] Failed to delete etcd member [etcd-master1] from etcd cluster"
time="2022-06-12T14:34:21+08:00" level=info msg="[remove/etcd] Removing member [etcd-master2] from etcd cluster"
time="2022-06-12T14:34:36+08:00" level=warning msg="[reconcile] Failed to delete etcd member [etcd-master2] from etcd cluster"
time="2022-06-12T14:34:36+08:00" level=info msg="[reconcile] Check etcd hosts to be added"
time="2022-06-12T14:34:36+08:00" level=info msg="[hosts] host [192.168.31.101] has another role, skipping delete from kubernetes cluster"
time="2022-06-12T14:34:36+08:00" level=info msg="[dialer] Setup tunnel for host [192.168.31.101]"
time="2022-06-12T14:34:42+08:00" level=info msg="[dialer] Setup tunnel for host [192.168.31.101]"
time="2022-06-12T14:34:45+08:00" level=info msg="[dialer] Setup tunnel for host [192.168.31.101]"
time="2022-06-12T14:34:48+08:00" level=warning msg="[reconcile] Couldn't clean up worker node [192.168.31.101]: Not able to reach the host: Can't retrieve Docker Info: error during connect: Get \"http://%2Fvar%2Frun%2Fdocker.sock/v1.24/info\": Failed to dial ssh using address [192.168.31.101:22]: dial tcp 192.168.31.101:22: connect: no route to host"
time="2022-06-12T14:34:48+08:00" level=info msg="[hosts] host [192.168.31.102] has another role, skipping delete from kubernetes cluster"
time="2022-06-12T14:34:48+08:00" level=info msg="[dialer] Setup tunnel for host [192.168.31.102]"
time="2022-06-12T14:34:52+08:00" level=info msg="[dialer] Setup tunnel for host [192.168.31.102]"
time="2022-06-12T14:34:55+08:00" level=info msg="[dialer] Setup tunnel for host [192.168.31.102]"
time="2022-06-12T14:34:58+08:00" level=warning msg="[reconcile] Couldn't clean up worker node [192.168.31.102]: Not able to reach the host: Can't retrieve Docker Info: error during connect: Get \"http://%2Fvar%2Frun%2Fdocker.sock/v1.24/info\": Failed to dial ssh using address [192.168.31.102:22]: dial tcp 192.168.31.102:22: connect: no route to host"
time="2022-06-12T14:34:58+08:00" level=info msg="[hosts] Cordoning host [192.168.31.101]"
time="2022-06-12T14:37:23+08:00" level=fatal msg="Failed to delete controlplane node [192.168.31.101] from cluster: etcdserver: request timed out"
```

另外，在挂掉两个 etcd 节点的 K8s 集群中，还可以读吗？我们执行 `kubectl get nodes` 时输出如下，（kubectl get nodes 默认是线性度？）
```s
[decent@master3 rke_test]$ kubectl get nodes
Error from server: etcdserver: request timed out
```

存活的 etcd 的部分日志如下
```s
2022-06-12 06:57:18.685178 W | etcdserver: read-only range request "key:\"/registry/services/endpoints/\" range_end:\"/registry/services/endpoints0\" limit:10000 " with result "error:etcdserver: request timed out" took too long (28.99883123s) to execute
2022-06-12 06:57:18.685198 W | etcdserver: read-only range request "key:\"/registry/secrets/\" range_end:\"/registry/secrets0\" limit:10000 " with result "error:etcdserver: request timed out" took too long (28.998864966s) to execute
2022-06-12 06:57:18.685224 W | etcdserver: read-only range request "key:\"/registry/services/specs/\" range_end:\"/registry/services/specs0\" limit:10000 " with result "error:etcdserver: request timed out" took too long (28.998895081s) to execute
2022-06-12 06:57:18.685245 W | etcdserver: read-only range request "key:\"/registry/podtemplates/\" range_end:\"/registry/podtemplates0\" limit:10000 " with result "error:etcdserver: request timed out" took too long (28.998917041s) to execute
2022-06-12 06:57:18.685264 W | etcdserver: read-only range request "key:\"/registry/controllers/\" range_end:\"/registry/controllers0\" limit:10000 " with result "error:etcdserver: request timed out" took too long (28.998939238s) to execute
2022-06-12 06:57:18.685425 W | etcdserver: read-only range request "key:\"/registry/serviceaccounts/\" range_end:\"/registry/serviceaccounts0\" limit:10000 " with result "error:etcdserver: request timed out" took too long (28.998643328s) to execute
2022-06-12 06:57:18.685499 W | etcdserver: read-only range request "key:\"/registry/volumeattachments/\" range_end:\"/registry/volumeattachments0\" limit:10000 " with result "error:etcdserver: request timed out" took too long (28.998717543s) to execute
2022-06-12 06:57:18.685665 W | etcdserver: read-only range request "key:\"/registry/clusterroles/\" range_end:\"/registry/clusterroles0\" count_only:true " with result "error:etcdserver: request timed out" took too long (29.433353442s) to execute
2022-06-12 06:57:18.686084 W | etcdserver: read-only range request "key:\"/registry/leases/\" range_end:\"/registry/leases0\" limit:10000 " with result "error:etcdserver: request timed out" took too long (28.998664316s) to execute
raft2022/06/12 06:57:19 INFO: 178e111d1072acde is starting a new election at term 317
raft2022/06/12 06:57:19 INFO: 178e111d1072acde became candidate at term 318
raft2022/06/12 06:57:19 INFO: 178e111d1072acde received MsgVoteResp from 178e111d1072acde at term 318
raft2022/06/12 06:57:19 INFO: 178e111d1072acde [logterm: 3, index: 2612] sent MsgVote request to 79d5043c5c6f48d3 at term 318
raft2022/06/12 06:57:19 INFO: 178e111d1072acde [logterm: 3, index: 2612] sent MsgVote request to affd5634331a4b9f at term 318
2022-06-12 06:57:22.369317 W | rafthttp: health check for peer affd5634331a4b9f could not connect: dial tcp 192.168.31.102:2380: connect: no route to host
2022-06-12 06:57:22.371569 W | rafthttp: health check for peer 79d5043c5c6f48d3 could not connect: dial tcp 192.168.31.101:2380: connect: no route to host
2022-06-12 06:57:22.573314 W | rafthttp: health check for peer 79d5043c5c6f48d3 could not connect: dial tcp 192.168.31.101:2380: connect: no route to host
2022-06-12 06:57:22.573347 W | rafthttp: health check for peer affd5634331a4b9f could not connect: dial tcp 192.168.31.102:2380: connect: no route to host
2022-06-12 06:57:25.928285 W | etcdserver: read-only range request "key:\"/registry/leases/kube-system/kube-controller-manager\" " with result "error:context canceled" took too long (9.997558193s) to execute
WARNING: 2022/06/12 06:57:25 grpc: Server.processUnaryRPC failed to write status: connection error: desc = "transport is closing"
raft2022/06/12 06:57:26 INFO: 178e111d1072acde is starting a new election at term 318
raft2022/06/12 06:57:26 INFO: 178e111d1072acde became candidate at term 319
raft2022/06/12 06:57:26 INFO: 178e111d1072acde received MsgVoteResp from 178e111d1072acde at term 319
raft2022/06/12 06:57:26 INFO: 178e111d1072acde [logterm: 3, index: 2612] sent MsgVote request to 79d5043c5c6f48d3 at term 319
raft2022/06/12 06:57:26 INFO: 178e111d1072acde [logterm: 3, index: 2612] sent MsgVote request to affd5634331a4b9f at term 319
2022-06-12 06:57:27.371441 W | rafthttp: health check for peer affd5634331a4b9f could not connect: dial tcp 192.168.31.102:2380: connect: no route to host
2022-06-12 06:57:27.371734 W | rafthttp: health check for peer 79d5043c5c6f48d3 could not connect: dial tcp 192.168.31.101:2380: connect: no route to host
2022-06-12 06:57:27.574519 W | rafthttp: health check for peer 79d5043c5c6f48d3 could not connect: dial tcp 192.168.31.101:2380: connect: no route to host
2022-06-12 06:57:27.574540 W | rafthttp: health check for peer affd5634331a4b9f could not connect: dial tcp 192.168.31.102:2380: connect: no route to host
2022-06-12 06:57:28.016739 W | etcdserver: read-only range request "key:\"/registry/leases/kube-system/kube-scheduler\" " with result "error:context canceled" took too long (9.999076457s) to execute
WARNING: 2022/06/12 06:57:28 grpc: Server.processUnaryRPC failed to write status: connection error: desc = "transport is closing"
2022-06-12 06:57:32.372016 W | rafthttp: health check for peer 79d5043c5c6f48d3 could not connect: dial tcp 192.168.31.101:2380: connect: no route to host
2022-06-12 06:57:32.372049 W | rafthttp: health check for peer affd5634331a4b9f could not connect: dial tcp 192.168.31.102:2380: connect: no route to host
2022-06-12 06:57:32.574907 W | rafthttp: health check for peer affd5634331a4b9f could not connect: dial tcp 192.168.31.102:2380: connect: no route to host
2022-06-12 06:57:32.574931 W | rafthttp: health check for peer 79d5043c5c6f48d3 could not connect: dial tcp 192.168.31.101:2380: connect: no route to host
2022-06-12 06:57:33.687093 W | etcdserver: timed out waiting for read index response (local node might have slow network)
```