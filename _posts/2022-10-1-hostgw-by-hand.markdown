---
layout:     post
title:      "手动配置 K8s hostgw 网络"
date:       2022-10-1 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - 网络
---
- [部署集群](#部署集群)
  - [配置网络](#配置网络)
  - [配置 subnet.env](#配置-subnetenv)
  - [配置路由](#配置路由)
  - [添加 iptables 规则](#添加-iptables-规则)
- [测试](#测试)
- [其他](#其他)

根据《[flannel host-gw 网络概述](https://loverhythm1990.github.io/2022/08/27/k8s-hostgw/)》，感觉 hostgw 网络模型还是比较简单的，于是萌生了一个想法，手动配置 hostgw 环境，不启用 flanneld，(也就是不启动 flannel daemon程序），手动配置路由，iptables 规则等，看看是不是行得通，以此更好地理解下 k8s 的网络模型。

实现环境共两个节点，hostname 和 ip 对应如下：

|  节点   | ip  |
|  ----  | ----  |
| HostGW-Master  | 192.168.31.201 |
| HostGW-Node  | 192.168.31.202 |

### 部署集群
通过 kubeadm 部署，参考《[使用 kubeadm 部署 Kubernetes](https://loverhythm1990.github.io/2022/08/26/kubeadm-k8s/)》
```s
kubeadm init --pod-network-cidr=10.244.0.0/16
```
启动之后，节点是 NotReady 状态，原因为：` message: 'runtime network not ready: NetworkReady=false reason:NetworkPluginNotReady, message:docker: network plugin is not ready: cni config uninitialized'`，这个错误原因是 dockershim 在初始化 defaultnetwork 的时候，没有找到网络配置。这个问题在通过添加 cni 网络配置文件解决，即下面的“配置网络”部分，理论上这个是需要 flannel 配置的，因为我们没有 flanneld，所以手动配置。

```s
[decent@HostGW-Master ~]$ kubectl get nodes
NAME            STATUS     ROLES    AGE     VERSION
hostgw-master   NotReady   master   9m18s   v1.16.9
hostgw-node     NotReady   <none>   3m28s   v1.16.9
```

#### 配置网络
在每个节点的 `/etc/cni/net.d` 目录配置网络，内容如下，每个节点都一样。这个目录对应 kubelet 的 `--cni-conf-dir` 参数。我们这里只配置了使用 flannel 作为我们的网络插件，其实现就是 /opt/cni/bin 目录下的 flannel 二进制文件。
> 注意，这个配置文件一定要用 `.conflist` 作为后缀，dockershim 的 cni 实现会查找后缀名为`.conf` `.conflist` `.json` 的配置文件，我们这里是一个插件列表，所以要用 `.conflist` 结尾。dockershim 的实现的路径为：`pkg/kubelet/dockershim/network/cni/cni.go`

```json
{
  "name": "cbr0",
  "cniVersion":"0.3.1",
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
```
配置这个文件之后，节点就为 Ready 状态了。但是很明显现在网络还是不能正常工作的。
```s
[decent@HostGW-Master net.d]$ kubectl get nodes -o wide
NAME            STATUS   ROLES    AGE   VERSION   INTERNAL-IP      EXTERNAL-IP   OS-IMAGE                KERNEL-VERSION                CONTAINER-RUNTIME
hostgw-master   Ready    master   19m   v1.16.9   192.168.31.201   <none>        CentOS Linux 7 (Core)   3.10.0-1160.76.1.el7.x86_64   docker://20.10.17
hostgw-node     Ready    <none>   13m   v1.16.9   192.168.31.202   <none>        CentOS Linux 7 (Core)   3.10.0-1160.76.1.el7.x86_64   docker://20.10.17
```
另外需要注意下 `/opt/cni/bin` 目录，是否有 cni 的 binary，如果没有的话，从 flannel 的 init 容器中拷贝一份。

#### 配置 subnet.env
这个是 flanneld 生成的，根据每个节点的 Node CIDR 生成 Pod 的子网，因为我们没有 flanneld，所以我们手动生成，路径和文件名是固定的。全路径为：`/var/run/flannel/subnet.env`。这里 flanneld 与 flannel 插件可以看成构成了一个生产者和消费者的模型，flanneld 生产这个文件，flannel 插件消费这个文件。同时，flanneld 除了生成这个文件，还会配置路由和 iptables 规则，就是我们下面要操作的内容。

首先看下 pod cidr:
```s
[decent@HostGW-Master net.d]$ kubectl get node hostgw-master -o yaml | grep CIDR
  podCIDR: 10.244.0.0/24
  podCIDRs:
[decent@HostGW-Master net.d]$ kubectl get node hostgw-node -o yaml | grep CIDR      
  podCIDR: 10.244.1.0/24
  podCIDRs:
[decent@HostGW-Master net.d]$ 
```

对于 `hostgw-master`，其配置为：(这里 mtu 设置成 1500 应该也没有问题，主要是在 overlay 网络下，要加上封包带来的开销，比如 vxlan 下要设置成 1450)
```s
FLANNEL_NETWORK=10.244.0.0/16
FLANNEL_SUBNET=10.244.0.1/24
FLANNEL_MTU=1450
FLANNEL_IPMASQ=true
```
对于 `hostgw-node`，其配置为
```s
FLANNEL_NETWORK=10.244.0.0/16
FLANNEL_SUBNET=10.244.1.1/24
FLANNEL_MTU=1450
FLANNEL_IPMASQ=true
```

#### 配置路由
根据 hostgw 工作方式，在每个节点上，配置 pod 网段的网管，以我们的环境为例，`HostGW-Master` 这个节点的网段为 `10.244.0.0/24`，那么所有其他所有的节点都要配置一条路由，即 `10.244.0.0/24` 的下一条为 `HostGW-Master` 的地址。

配置路由之前的路由列表如下（HostGW-Master 节点），路由表中，还有个路由比较重要 `10.244.0.0/24 dev cni0 proto kernel scope link src 10.244.0.1`，这个路由是 flannel 插件配置的，对于所有的目标地址为 `10.244.0.0/24` 的报文，都发送到 `cni0` 这个网桥，再由网桥发生给具体的 pod。
```s
[decent@HostGW-Master net.d]$ ip route
default via 192.168.31.1 dev ens33 proto static metric 100 
10.244.0.0/24 dev cni0 proto kernel scope link src 10.244.0.1 
172.17.0.0/16 dev docker0 proto kernel scope link src 172.17.0.1 
192.168.31.0/24 dev ens33 proto kernel scope link src 192.168.31.201 metric 100 
```
在两个节点分别添加路由：
```s
# master 节点
sudo ip route add 10.244.1.0/24 via 192.168.31.202 dev ens33

# worker 节点
sudo ip route add 10.244.0.0/24 via 192.168.31.201 dev ens33
```

#### 添加 iptables 规则

在 filter 表的 forward 链添加对 pod 子网的转发，对 pod 所有ip 都进行转发。确认添加之前的 FORWARD 链。
```s
[decent@HostGW-Master ~]$ sudo iptables -v -L FORWARD
Chain FORWARD (policy DROP 14 packets, 630 bytes)
 pkts bytes target     prot opt in     out     source               destination         
 4649  210K KUBE-FORWARD  all  --  any    any     anywhere             anywhere             /* kubernetes forwarding rules */
 4649  210K KUBE-SERVICES  all  --  any    any     anywhere             anywhere             ctstate NEW /* kubernetes service portals */
 4649  210K DOCKER-USER  all  --  any    any     anywhere             anywhere            
 4649  210K DOCKER-ISOLATION-STAGE-1  all  --  any    any     anywhere             anywhere            
    0     0 ACCEPT     all  --  any    docker0  anywhere             anywhere             ctstate RELATED,ESTABLISHED
    0     0 DOCKER     all  --  any    docker0  anywhere             anywhere            
    0     0 ACCEPT     all  --  docker0 !docker0  anywhere             anywhere            
    0     0 ACCEPT     all  --  docker0 docker0  anywhere             anywhere   
```
添加如下的 iptables 规则。因为针对的是集群整个 pod 的 CIDR，所以每个节点都是一样的。
```s
sudo iptables -A FORWARD -s 10.244.0.0/16 -j ACCEPT
sudo iptables -A FORWARD -d 10.244.0.0/16 -j ACCEPT 
```
> 一开始在实验过程中并没有设置这个 iptables 规则，所以网络一直不通。因此在需要 linux 执行转发的时候，除了打开 ip_forward，可能还需要在对应的 forward 链添加规则。

查看添加之后的 FORWARD 链，最关键的是后面两条。
```s
[decent@HostGW-Master ~]$ sudo iptables -v -L FORWARD                        
Chain FORWARD (policy DROP 0 packets, 0 bytes)
 pkts bytes target     prot opt in     out     source               destination         
 4854  220K KUBE-FORWARD  all  --  any    any     anywhere             anywhere             /* kubernetes forwarding rules */
 4854  220K KUBE-SERVICES  all  --  any    any     anywhere             anywhere             ctstate NEW /* kubernetes service portals */
 4854  220K DOCKER-USER  all  --  any    any     anywhere             anywhere            
 4854  220K DOCKER-ISOLATION-STAGE-1  all  --  any    any     anywhere             anywhere            
    0     0 ACCEPT     all  --  any    docker0  anywhere             anywhere             ctstate RELATED,ESTABLISHED
    0     0 DOCKER     all  --  any    docker0  anywhere             anywhere            
    0     0 ACCEPT     all  --  docker0 !docker0  anywhere             anywhere            
    0     0 ACCEPT     all  --  docker0 docker0  anywhere             anywhere            
   73  3285 ACCEPT     all  --  any    any     10.244.0.0/16        anywhere            
    0     0 ACCEPT     all  --  any    any     anywhere             10.244.0.0/16       
```

### 测试
创建几个 pod，在 master 节点 ping worker 节点的 pod，看看能不能 ping 通，证明方案是可行的。
```s
[decent@HostGW-Master ~]$ kubectl get pods -o wide
NAME                                READY   STATUS    RESTARTS   AGE   IP           NODE            NOMINATED NODE   READINESS GATES
nginx-deployment-574b87c764-fffsl   1/1     Running   0          5s    10.244.1.2   hostgw-node     <none>           <none>
nginx-deployment-574b87c764-px7g5   1/1     Running   0          5s    10.244.0.4   hostgw-master   <none>           <none>
nginx-deployment-574b87c764-xwbpp   1/1     Running   0          5s    10.244.1.3   hostgw-node     <none>           <none>
[decent@HostGW-Master ~]$ ping 10.244.1.3
PING 10.244.1.3 (10.244.1.3) 56(84) bytes of data.
64 bytes from 10.244.1.3: icmp_seq=1 ttl=63 time=0.509 ms
64 bytes from 10.244.1.3: icmp_seq=2 ttl=63 time=0.361 ms
^C
--- 10.244.1.3 ping statistics ---
2 packets transmitted, 2 received, 0% packet loss, time 1001ms
rtt min/avg/max/mdev = 0.361/0.435/0.509/0.074 ms

# 访问也是成功的
[decent@HostGW-Master ~]$ curl  10.244.1.2
<!DOCTYPE html>
<html>
<head>
<title>Welcome to nginx!</title>
<style>
    body {
        width: 35em;
        margin: 0 auto;
        font-family: Tahoma, Verdana, Arial, sans-serif;
    }
</style>
</head>
<body>
<h1>Welcome to nginx!</h1>
<p>If you see this page, the nginx web server is successfully installed and
working. Further configuration is required.</p>

<p>For online documentation and support please refer to
<a href="http://nginx.org/">nginx.org</a>.<br/>
Commercial support is available at
<a href="http://nginx.com/">nginx.com</a>.</p>

<p><em>Thank you for using nginx.</em></p>
</body>
</html>
```

### 其他
用到的一些 iptables 命令，查看 iptables （某个表）的某个链，并显示行号，并可以根据这些行号来删除规则。
```s
sudo iptables -L FORWARD -n -v --line-numbers

# 删除 FORWARD 链的第三条规则
sudo iptables -D FORWARD 3
```

添加、查看网桥，具体看《Kubernetes 网络权威指南》
```s
brctl addbr br0 
# 或者
ip link add name br0 type bridge
ip link set br0 up
```

使用 `traceroute` 查看 ip 路由过程，[https://serverfault.com/questions/334029/what-does-mean-when-traceroute](https://serverfault.com/questions/334029/what-does-mean-when-traceroute)
```s
[decent@HostGW-Master ~]$ traceroute 10.42.1.97
traceroute to 10.42.1.97 (10.42.1.97), 30 hops max, 60 byte packets
 1  192.168.31.202 (192.168.31.202)  0.254 ms  0.260 ms  0.218 ms
 2  * * *
 3  * * *
 4  * * *
 5  * * *
 6  * * *
 7  *^C
```

从网卡 ping IP。
```s
[decent@HostGW-Node tcpdump]$ ping -c 1 -I ens33 10.244.1.2
PING 10.244.1.2 (10.244.1.2) from 192.168.31.202 ens33: 56(84) bytes of data.

--- 10.244.1.2 ping statistics ---
1 packets transmitted, 0 received, 100% packet loss, time 0ms
```