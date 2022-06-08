---
layout:     post
title:      "Etcd 在 rke 环境下通过 force-new-cluster 实现灾备-操作记录"
date:       2022-6-8 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - etcd
---

### 集群初始状态
K8s 集群信息，master1/master2/master3 是管控节点，node1、node2是 worker 节点，其中 master3/node2 位于 B 机房。
```s
[decent@master1 ~]$ kubectl get nodes -o wide
NAME      STATUS   ROLES                      AGE     VERSION   INTERNAL-IP      EXTERNAL-IP   OS-IMAGE                KERNEL-VERSION           CONTAINER-RUNTIME
master1   Ready    controlplane,etcd,worker   9m25s   v1.20.5   192.168.31.101   <none>        CentOS Linux 7 (Core)   3.10.0-1160.el7.x86_64   docker://20.10.16
master2   Ready    controlplane,etcd,worker   9m25s   v1.20.5   192.168.31.102   <none>        CentOS Linux 7 (Core)   3.10.0-1160.el7.x86_64   docker://20.10.16
master3   Ready    controlplane,etcd,worker   9m25s   v1.20.5   192.168.31.103   <none>        CentOS Linux 7 (Core)   3.10.0-1160.el7.x86_64   docker://20.10.13
node1     Ready    worker                     9m25s   v1.20.5   192.168.31.104   <none>        CentOS Linux 7 (Core)   3.10.0-1160.el7.x86_64   docker://20.10.13
node2     Ready    worker                     9m25s   v1.20.5   192.168.31.105   <none>        CentOS Linux 7 (Core)   3.10.0-1160.el7.x86_64   docker://20.10.13
```

etcd 集群信息
```s
[decent@master1 ~]$ sudo etcdctl --endpoints=https://192.168.31.101:2379,https://192.168.31.102:2379,https://192.168.31.103:2379 --cert=/etc/kubernetes/ssl/kube-etcd-192-168-31-101.pem --key=/etc/kubernetes/ssl/kube-etcd-192-168-31-101-key.pem --cacert=/etc/kubernetes/ssl/kube-ca.pem endpoint status --write-out=table
+-----------------------------+------------------+---------+---------+-----------+------------+-----------+------------+--------------------+--------+
|          ENDPOINT           |        ID        | VERSION | DB SIZE | IS LEADER | IS LEARNER | RAFT TERM | RAFT INDEX | RAFT APPLIED INDEX | ERRORS |
+-----------------------------+------------------+---------+---------+-----------+------------+-----------+------------+--------------------+--------+
| https://192.168.31.101:2379 | 79d5043c5c6f48d3 |  3.4.14 |  3.9 MB |      true |      false |         3 |       3166 |               3166 |        |
| https://192.168.31.102:2379 | affd5634331a4b9f |  3.4.14 |  3.9 MB |     false |      false |         3 |       3166 |               3166 |        |
| https://192.168.31.103:2379 | 178e111d1072acde |  3.4.14 |  3.8 MB |     false |      false |         3 |       3166 |               3166 |        |
+-----------------------------+------------------+---------+---------+-----------+------------+-----------+------------+--------------------+--------+
```

部署了两个应用，状态为：
```s
[decent@master1 ~]$ kubectl get pods -o wide 
NAME                READY   STATUS    RESTARTS   AGE   IP           NODE      NOMINATED NODE   READINESS GATES
hello-world-29v64   1/1     Running   0          11m   10.42.2.30   master1   <none>           <none>
hello-world-rg2lw   1/1     Running   0          11m   10.42.1.49   master2   <none>           <none>
```

### 模拟灾备
灾害发生前的前置工作为需要将 master3 配置为部署机，并且拥有 rke up 命令所需要的的配置文件（有个细节需要注意一下，master3节点的 kubeconfig 地址需要指向 master3 的kube-apiserver）。

将 master1/master2/node1 断电。


### force new cluster
在 master3 执行下面命令，并得到输出：
```s
[decent@master3 rke]$ docker run --rm -v /var/run/docker.sock:/var/run/docker.sock assaflavie/runlike etcd

docker run --name=etcd --hostname=master3 --env=ETCDCTL_API=3 --env=ETCDCTL_CACERT=/etc/kubernetes/ssl/kube-ca.pem --env=ETCDCTL_CERT=/etc/kubernetes/ssl/kube-etcd-192-168-31-103.pem --env=ETCDCTL_KEY=/etc/kubernetes/ssl/kube-etcd-192-168-31-103-key.pem --env=ETCDCTL_ENDPOINTS=https://127.0.0.1:2379 --env=ETCD_UNSUPPORTED_ARCH=x86_64 --env=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin --volume=/var/lib/etcd:/var/lib/rancher/etcd/:z --volume=/etc/kubernetes:/etc/kubernetes:z --network=host --restart=always --label='io.rancher.rke.container.name=etcd' --runtime=runc --detach=true rancher/coreos-etcd:v3.4.14-rancher1 /usr/local/bin/etcd --listen-client-urls=https://0.0.0.0:2379 --initial-advertise-peer-urls=https://192.168.31.103:2380 --initial-cluster-state=new --trusted-ca-file=/etc/kubernetes/ssl/kube-ca.pem --peer-key-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-103-key.pem --advertise-client-urls=https://192.168.31.103:2379 --initial-cluster-token=etcd-cluster-1 --listen-peer-urls=https://0.0.0.0:2380 --initial-cluster=etcd-master1=https://192.168.31.101:2380,etcd-master2=https://192.168.31.102:2380,etcd-master3=https://192.168.31.103:2380 --peer-trusted-ca-file=/etc/kubernetes/ssl/kube-ca.pem --key-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-103-key.pem --election-timeout=5000 --heartbeat-interval=500 --data-dir=/var/lib/rancher/etcd/ --client-cert-auth=true --peer-client-cert-auth=true --name=etcd-master3 --cert-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-103.pem --peer-cert-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-103.pem --enable-v2=true
```

需要对上面命令进行修改，需要修改的地方有：1）`--initial-cluster`修改为单节点，即：--initial-cluster=etcd-master3=https://192.168.31.103:2380，2）添加参数 --force-new-cluster。修改后的命令为：
```s
docker run --name=etcd --hostname=master3 --env=ETCDCTL_API=3 --env=ETCDCTL_CACERT=/etc/kubernetes/ssl/kube-ca.pem --env=ETCDCTL_CERT=/etc/kubernetes/ssl/kube-etcd-192-168-31-103.pem --env=ETCDCTL_KEY=/etc/kubernetes/ssl/kube-etcd-192-168-31-103-key.pem --env=ETCDCTL_ENDPOINTS=https://127.0.0.1:2379 --env=ETCD_UNSUPPORTED_ARCH=x86_64 --env=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin --volume=/var/lib/etcd:/var/lib/rancher/etcd/:z --volume=/etc/kubernetes:/etc/kubernetes:z --network=host --restart=always --label='io.rancher.rke.container.name=etcd' --runtime=runc --detach=true rancher/coreos-etcd:v3.4.14-rancher1 /usr/local/bin/etcd --listen-client-urls=https://0.0.0.0:2379 --initial-advertise-peer-urls=https://192.168.31.103:2380 --initial-cluster-state=new --trusted-ca-file=/etc/kubernetes/ssl/kube-ca.pem --peer-key-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-103-key.pem --advertise-client-urls=https://192.168.31.103:2379 --initial-cluster-token=etcd-cluster-1 --listen-peer-urls=https://0.0.0.0:2380 --initial-cluster=etcd-master3=https://192.168.31.103:2380 --peer-trusted-ca-file=/etc/kubernetes/ssl/kube-ca.pem --key-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-103-key.pem --election-timeout=5000 --heartbeat-interval=500 --data-dir=/var/lib/rancher/etcd/ --client-cert-auth=true --peer-client-cert-auth=true --name=etcd-master3 --cert-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-103.pem --peer-cert-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-103.pem --enable-v2=true --force-new-cluster
```

备份原来的 etcd 容器。
```s
docker stop etcd
docker rename etcd etcd-old
```

使用上面的命令启动容器，另外使用 `docker inspect etcd` 查看新启动的容器，其参数如下，注意带着 `--force-new-cluster` 参数。
```s
        "Id": "691027b0060042457ceaf766df7da029e0d730af4aa3f692e93acfc8d5a8ab77",
        "Created": "2022-06-08T08:22:58.304237386Z",
        "Path": "/usr/local/bin/etcd",
        "Args": [
            "--listen-client-urls=https://0.0.0.0:2379",
            "--initial-advertise-peer-urls=https://192.168.31.103:2380",
            "--initial-cluster-state=new",
            "--trusted-ca-file=/etc/kubernetes/ssl/kube-ca.pem",
            "--peer-key-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-103-key.pem",
            "--advertise-client-urls=https://192.168.31.103:2379",
            "--initial-cluster-token=etcd-cluster-1",
            "--listen-peer-urls=https://0.0.0.0:2380",
            "--initial-cluster=etcd-master3=https://192.168.31.103:2380",
            "--peer-trusted-ca-file=/etc/kubernetes/ssl/kube-ca.pem",
            "--key-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-103-key.pem",
            "--election-timeout=5000",
            "--heartbeat-interval=500",
            "--data-dir=/var/lib/rancher/etcd/",
            "--client-cert-auth=true",
            "--peer-client-cert-auth=true",
            "--name=etcd-master3",
            "--cert-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-103.pem",
            "--peer-cert-file=/etc/kubernetes/ssl/kube-etcd-192-168-31-103.pem",
            "--enable-v2=true",
            "--force-new-cluster"
        ],
``` 

### 检查集群状态
此时，集群 Node 状态为，断电的三个节点，其状态为 `NotReady`。
```s
[decent@master3 ~]$ kubectl get nodes -o wide
NAME      STATUS     ROLES                      AGE    VERSION   INTERNAL-IP      EXTERNAL-IP   OS-IMAGE                KERNEL-VERSION           CONTAINER-RUNTIME
master1   NotReady   controlplane,etcd,worker   141m   v1.20.5   192.168.31.101   <none>        CentOS Linux 7 (Core)   3.10.0-1160.el7.x86_64   docker://20.10.16
master2   NotReady   controlplane,etcd,worker   141m   v1.20.5   192.168.31.102   <none>        CentOS Linux 7 (Core)   3.10.0-1160.el7.x86_64   docker://20.10.16
master3   Ready      controlplane,etcd,worker   141m   v1.20.5   192.168.31.103   <none>        CentOS Linux 7 (Core)   3.10.0-1160.el7.x86_64   docker://20.10.13
node1     NotReady   worker                     141m   v1.20.5   192.168.31.104   <none>        CentOS Linux 7 (Core)   3.10.0-1160.el7.x86_64   docker://20.10.13
node2     Ready      worker                     141m   v1.20.5   192.168.31.105   <none>        CentOS Linux 7 (Core)   3.10.0-1160.el7.x86_64   docker://20.10.13
```

但是这个时候，我们使用 `kubectl get node master1 -o yaml` 命令来查看节点状态，发现节点没有 `NotReady` 相关污点，其所有的污点信息如下，只有一个 `NoSchedule` 的污点，这个集群的 K8s 版本为`v1.20.5` 不知道是不是跟 K8s 版本有关。
```yml
spec:
  podCIDR: 10.42.2.0/24
  podCIDRs:
  - 10.42.2.0/24
  taints:
  - effect: NoSchedule
    key: node.kubernetes.io/unreachable
    timeAdded: "2022-06-08T08:24:21Z"
```

我们再看下 pod 状态，这个状态是 `--force-new-cluster` 执行很久之后的状态，注意 `master2` 已经断电很久了，但是上面的 pod 并没有发生驱逐。
```s
[decent@master3 ~]$ kubectl get pods -o wide
NAME                READY   STATUS        RESTARTS   AGE    IP           NODE      NOMINATED NODE   READINESS GATES
hello-world-29v64   1/1     Terminating   0          142m   10.42.2.30   master1   <none>           <none>
hello-world-pwk5h   1/1     Running       0          96m    10.42.4.57   node2     <none>           <none>
hello-world-rg2lw   1/1     Running       0          142m   10.42.1.49   master2   <none>           <none>
```

我们看下 pod `hello-world-rg2lw` 的 tolerations，不驱逐是合理的，这个时候节点没有打 `node.kubernetes.io/not-ready` 或者 `node.kubernetes.io/unreachable` 的污点。【感觉这个地方 K8s 的处理有点问题，需要验证 1.16 版本下，K8s 是否也有这个问题】
```yml
  tolerations:
  - effect: NoExecute
    key: node.kubernetes.io/not-ready
    operator: Exists
    tolerationSeconds: 300
  - effect: NoExecute
    key: node.kubernetes.io/unreachable
    operator: Exists
    tolerationSeconds: 300
```

这个时候，我们再创建一个应用，可以看到被调度到了 master3 上。
```s
[decent@master3 resource]$ kubectl get pods -owide | grep pods
NAME                READY   STATUS        RESTARTS   AGE    IP           NODE      NOMINATED NODE   READINESS GATES
pods-simple-pod     1/1     Running       0          12s    10.42.0.23   master3   <none>           <none>
```

### 移除A机房节点
移除 A 机房节点，需要在 rke 命令中执行。我们先尝试移除部分解决，比如先移除 master1 节点。在 `cluster.yml` 文件中删除 master1 节点的配置，然后执行 `rke up --config cluster.yml`。`rke up` 执行过程中，出现了很多错误，最终也出现了 FATA 错误。
```s
WARN[0039] [reconcile] Couldn't clean up controlplane node [192.168.31.101]: Not able to reach the host: Can't retrieve Docker Info: error during connect: Get "http://%2Fvar%2Frun%2Fdocker.sock/v1.24/info": Failed to dial ssh using address [192.168.31.101:22]: dial tcp 192.168.31.101:22: connect: no route to host 

INFO[0049] Removing container [rke-log-linker] on host [192.168.31.103], try #1 
INFO[0049] [remove/rke-log-linker] Successfully removed container on host [192.168.31.103] 
INFO[0049] [etcd] Successfully started etcd plane.. Checking etcd cluster health 
INFO[0051] [etcd] etcd host [192.168.31.103] reported healthy=true 
FATA[0051] cannot proceed with upgrade of controlplane since 1 host(s) cannot be reached prior to upgrade 
```

但是执行完之后，看到 master1 确实被删除了
```s
[decent@master3 rke_test]$ kubectl get nodes
NAME      STATUS     ROLES                      AGE     VERSION
master2   NotReady   controlplane,etcd,worker   4h50m   v1.20.5
master3   Ready      controlplane,etcd,worker   4h50m   v1.20.5
node1     NotReady   worker                     4h50m   v1.20.5
node2     Ready      worker                     4h50m   v1.20.5
```

但是，`docker inspect kube-apiserver` 看到 apiserver 参数 `--etcd-servers`中，仍然包含 `192.168.31.101:2379`，也就是我们移除的 master1 节点上的 etcd 地址。
```s
 "--etcd-servers=https://192.168.31.103:2379,https://192.168.31.101:2379,https://192.168.31.102:2379",
```

> 这个时候，想到了一个办法，能不能使用 `assaflavie/runlike` 镜像，修改下 kube-apiserver 的参数，然后重新启动下？【后面试一下】

我们这里先把 A 机房所有的节点都移除下，修改下 `cluster.yml`，删除 master2 和 node1 配置，再执行下 rke up。这次显示执行成功。
```s
INFO[0133] [addons] Saving ConfigMap for addon rke-ingress-controller to Kubernetes 
INFO[0133] [addons] Successfully saved ConfigMap for addon rke-ingress-controller to Kubernetes 
INFO[0133] [addons] Executing deploy job rke-ingress-controller 
INFO[0133] [ingress] ingress controller nginx deployed successfully 
INFO[0133] [addons] Setting up user addons              
INFO[0133] [addons] no user addons defined              
INFO[0133] Finished building Kubernetes cluster successfully
```

看下节点以及 pod。都是正常的
```s
[decent@master3 rke_test]$ kubectl get nodes
NAME      STATUS   ROLES                      AGE     VERSION
master3   Ready    controlplane,etcd,worker   5h24m   v1.20.5
node2     Ready    worker                     5h24m   v1.20.5
[decent@master3 rke_test]$ kubectl get pods -o wide
NAME                READY   STATUS    RESTARTS   AGE     IP           NODE      NOMINATED NODE   READINESS GATES
hello-world-bjwc6   1/1     Running   0          29m     10.42.4.58   node2     <none>           <none>
hello-world-pwk5h   1/1     Running   0          4h34m   10.42.4.57   node2     <none>           <none>
pods-simple-pod     1/1     Running   2          163m    10.42.0.23   master3   <none>           <none>
```

再使用 docker inspect 检查下 `kube-apiserver` 的参数，这个时候 `--etcd-servers` 参数是正常的:
```s
"--etcd-servers=https://192.168.31.103:2379",
```

再使用 docker inspect 检查 `etcd` 的参数，发现 `--force-new-cluster` 参数也没有了，【**由此可见，rke up --config cluster.yml 对 etcd 的参数有修正？**，可以进一步验证下】

用以访问 `kube-apiserver` 的 `kubernetes` 服务也是正常的。
```s
[decent@master3 rke_test]$ kubectl get service
NAME          TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)        AGE
hello-world   NodePort    10.43.177.135   <none>        80:31117/TCP   5h24m
kubernetes    ClusterIP   10.43.0.1       <none>        443/TCP        5h29m
[decent@master3 rke_test]$ kubectl get endpoints
NAME          ENDPOINTS                         AGE
hello-world   10.42.4.57:8080,10.42.4.58:8080   5h25m
kubernetes    192.168.31.103:6443               5h29m
```

### 尝试重启 kube-apiserver，修改 --etcd-servers 参数
我们使用 `docker run --rm -v /var/run/docker.sock:/var/run/docker.sock assaflavie/runlike kube-apiserver` 命令得到 kube-apiserver 的运行参数如下：

```s
docker run --name=kube-apiserver --hostname=master3 --env=RKE_AUDITLOG_CONFIG_CHECKSUM=856f426399fb14a50b78e721d15c168c --env=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin --volume=/etc/kubernetes:/etc/kubernetes:z --volume=/var/log/kube-audit:/var/log/kube-audit:z --volumes-from=service-sidekick --network=host --restart=always --label='org.label-schema.vcs-ref=2fad0cab4ec1801f91ac2842ad453849690380c6' --label='org.opencontainers.image.url=https://github.com/rancher/hyperkube' --label='org.label-schema.schema-version=1.0' --label='io.rancher.rke.container.name=kube-apiserver' --label='org.label-schema.build-date=2021-01-28T17:44:52Z' --label='org.opencontainers.image.source=https://github.com/rancher/hyperkube.git' --label='org.opencontainers.image.created=2021-03-29T18:28:09Z' --label='org.opencontainers.image.revision=7589f9f8ba4a48a38f243bfc8a7d951db0fb3f25' --label='org.label-schema.vcs-url=https://github.com/rancher/hyperkube-base.git' --runtime=runc --detach=true rancher/hyperkube:v1.20.5-rancher1
```

【发现生成的参数是不合理的！！！】不过也为我们提供了一个思路，通过替换容器来修改容器的参数，因为我们总是可以找到正确的参数

