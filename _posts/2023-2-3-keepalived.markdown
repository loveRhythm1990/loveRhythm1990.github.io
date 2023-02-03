---
layout:     post
title:      "通过 Keepalived 实现应用高可用"
date:       2023-2-3 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - 网络
---

当应用实例部署在多个节点的时候，可以通过 Keepalived 来实现高可用，Keepalived 实现高可用的方式是主备模式，也就是只有一个 master 在工作，其余的节点处在 backup 状态，master 节点挂掉时，备节点变成 master 节点继续提供服务。在配置高可用时，首先得有一个 vip，初始情况下， vip 配置在 master 节点的网卡上，master 节点故障后，会漂移到其他节点。

在 K8s 环境中，当应用通过 NodePort 提供服务时，可以通过 Keepalived 实现简单的高可用，如果一个 service 声明为 NodePort 类型，访问任何一个机器的 ip+NodePort 其实都是可以提供服务的。但是我们也不能跟应用说 “你访问随便一个就好了”，也不能提供一个固定的机器 ip 给他，万一这个机器挂了呢？这个时候，我们就可以给他一个 vip，让他访问这个 vip，然后我们通过 Keepalived 将这个 vip 和几台机器绑定，来实现高可用。Keepalived 中的 master 会定期发送 VRRP 报文给 backup，默认周期是 1s；如果 backup 实例连续 3s 没有收到 master 发的报文，配置了最高优先级的 backup 实例将提升为 master。

本文通过下面几台实例来测试 Keepalived 功能。选定的 vip 为 `192.168.31.250`，要保证没有设备在使用这个 vip，并且这个 vip 在未来也不会被分配。

|  初始状态   | 节点 ip  | 
|  ----  | ----  | 
| master | 192.168.31.203 |
| backup | 192.168.31.201 | 
| backup  | 192.168.31.202 | 

> 一般在企业测试和生产环境中，vip 都是网关同学给分配的，vip 可能跟主机不是一个网段，但相关主机和路由设备都应配置对应的路由策略，（不然访问 vip 时可能 `no route to host`），另外还要保证 vip 不会被 dhcp 分配，在此测试环境中，选定的 `192.168.31.250` 乐观的认为不会被分配给家里的手机/平板等。

### 安装配置
如果机器没有安装 keepalived，可以通过下面命令安装。
```s
sudo yum install keepalived
```
#### master 节点配置
在节点 `192.168.31.203`，编辑 `/etc/keepalived/keepalived.conf` 文件，内容配置如下，这里是最简配置，其中几个字段解释如下：
* state: 初始状态，可选为 `MASTER`、`BACKUP`。
* interface: 绑定节点的哪个网卡，需要看下自己节点的网卡名称。
* virtual_router_id: 每个 Keepalived 都匹配配置成一样的。
* priority: 优先级，优先级越高的，首先成为 master.
* virtual_ipaddress: vip，可以配置多个。

修改 `/etc/keepalived/keepalived.conf` 文件的全部内容如下：
```s
vrrp_instance VRRP1 {
    state MASTER
    interface ens33   
    virtual_router_id 41
    priority 200
    advert_int 1
    virtual_ipaddress {
        192.168.31.250/24
    }
}
```
重启 Keepalived 以及查看日志
```s
systemctl restart keepalived
systemctl status keepalived
journalctl -u keepalived -f
```

#### backup 节点配置
backup 配置基本跟 master 节点一致，state 要改成 `BACKUP`，priority 可以改一下（不改的话，会根据 ip 大小来决定优先级）。还是修改文件 `/etc/keepalived/keepalived.conf`。
```s
vrrp_instance VRRP1 {
    state BACKUP
    interface ens33   
    virtual_router_id 41
    priority 199
    advert_int 1
    virtual_ipaddress {
        192.168.31.250/24
    }
}
```
另一个 backup 节点的配置类似，基本不需要变动。

### 观察配置
在 master 节点 `192.168.31.203`，发现这个 vip 被绑定在了 ens33 这个网卡上，在另个两个节点上是没有的。
```s
[decent@HostGW-Node2 ~]$ ip addr show ens33
2: ens33: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc pfifo_fast state UP group default qlen 1000
    link/ether 00:50:56:3c:e3:b9 brd ff:ff:ff:ff:ff:ff
    inet 192.168.31.203/24 brd 192.168.31.255 scope global noprefixroute ens33
       valid_lft forever preferred_lft forever
    inet 192.168.31.250/24 scope global secondary ens33
       valid_lft forever preferred_lft forever
    inet6 fe80::7ce6:3c4:2945:27cd/64 scope link noprefixroute
       valid_lft forever preferred_lft forever
```

### 验证测试
我们首先 ping 一下我们的 vip，比如我在自己的 mac 中 ping 一下。（测试的三台机器是 windows 系统下的三台虚拟机）。通过下面输出，发现是通的。
```s
decent@Mac loveRhythm1990.github.io % ping 192.168.31.250
PING 192.168.31.250 (192.168.31.250): 56 data bytes
64 bytes from 192.168.31.250: icmp_seq=0 ttl=64 time=21.898 ms
64 bytes from 192.168.31.250: icmp_seq=1 ttl=64 time=8.718 ms
64 bytes from 192.168.31.250: icmp_seq=2 ttl=64 time=8.374 ms
^C
--- 192.168.31.250 ping statistics ---
3 packets transmitted, 3 packets received, 0.0% packet loss
round-trip min/avg/max/stddev = 8.374/12.997/21.898/6.296 ms
```

我们启动一个 echo 服务，并配置一个 nodeport 服务访问试一下。nginx 服务的 yaml：
```yml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: echo-deployment
spec:
  replicas: 1
  selector:
    matchLabels:
      app: echo-server
  template:
    metadata:
      labels:
        app: echo-server
    spec:
      containers:
        - name: echo-server
          image: docker.io/agile6v/e2e-test-echo:latest
          ports:
            - name: http-port
              containerPort: 80
---
apiVersion: v1
kind: Service
metadata:
  name: echo-service
spec:
  type: NodePort
  ports:
    - name: http-port
      port: 80
      targetPort: http-port
      protocol: TCP
  selector:
    app: echo-server
```
> 上面镜像 `docker.io/agile6v/e2e-test-echo:latest` 是一个简单的 echo 服务器，来自 kubernetes ingress controller 的 e2e 测试。源镜像为：`registry.k8s.io/ingress-nginx/e2e-test-echo@sha256:778ac6d1188c8de8ecabeddd3c37b72c8adc8c712bad2bd7a81fb23a3514934c`

查看 service 端口。
```s
[decent@HostGW-Master resources]$ kubectl get service
NAME           TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)        AGE
echo-service   NodePort    10.102.156.26   <none>        80:32463/TCP   8m51s
```

在 mac 上访问 vip，发现是通的，停掉 Keepalived master 之后，访问也是通的，可以通过查看 Keepalived 日志来观察实例角色变化。
```s
decent@Mac loveRhythm1990.github.io % curl http://192.168.31.250:32463/abc
Hostname: echo-deployment-6df5bfbfc-9s5hc
Pod Information:
	-no pod information available-
... 
```

### 其他
Keepalived 可以配置邮件通知、访问密码等，可参考相关文档

### 参考

[17.6 Configuring Simple Virtual IP Address Failover Using Keepalived](https://docs.oracle.com/en/operating-systems/oracle-linux/6/admin/section_uxg_lzh_nr.html#)
