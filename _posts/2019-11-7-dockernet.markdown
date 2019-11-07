---
layout:     post
title:      "单机容器网络"
subtitle:   " \"单机容器网络配置\""
date:       2019-11-7 8:24:00
author:     "weak old dog"
header-img-credit: false
tags:
    - k8s网络
---

单机容器网络的配置，记录并理解单机容器网络的基础知识，生产环境没有单机容器网络，但是这些是理解更复杂的网络模型的基础。

### 安装docker之后
安装docker之后，linux网络配置变化变化有三点：
1. 多了一个`docker0`网桥，这个是linux虚拟网桥。
    首先理解一下这个`docker0`网桥，根据[理解Docker单机容器网络](https://tonybai.com/2016/01/15/understanding-container-networking-on-single-host/)，对于本节点的容器来说，docker0扮演一个二层交换机的角色，拥有二层交换机的功能：泛洪，维护cam表，在二层转发数据包；同时docker0自身也有mac地址（这个与纯二层交换机不同），并且绑定了ip（172.17.0.1）因此还作为container默认gateway存在；对于宿主机来说，所有docker0从veth（作为端口存在，没有ip地址）接收的数据包都会被宿主机看成从docker0这块网卡（虚拟网卡 172.17.0.1）接收进来的数据包，尤其是在进入三层时，宿主机上的iptables就会 对docker0进来的数据包按照rules进行相应处理。
2. 多了一条路由规则，即下面以172开头的路由规则。

    `172.17.0.0 *   255.255.0.0 U 0 0 docker0`
     
     gateway为`*`或者`0.0.0.0`表示与本机IP同一网段，不需要经过网关（同一个局域网内2台主机通信不需要经过网关），该路由规则的输出接口为docker0。
3. 多了一些iptables规则
    在docker的默认网络模型下，会对container发出的包进行NAT转换，通过iptables的MASQUERADE规则实现的。MASQUERADE类似于SNAT，区别是它会从服务器的网卡上，自动获取当前ip地址来做NAT，不用手动制定转换后的地址或者地址范围。

这些变化可以通过下面几张图片来查看。

![java-javascript](/img/in-post/docker-net/3.png)
![java-javascript](/img/in-post/docker-net/4.png)
![java-javascript](/img/in-post/docker-net/5.png)
![java-javascript](/img/in-post/docker-net/6.png)
![java-javascript](/img/in-post/docker-net/7.png)

### 启动一个容器之后
![java-javascript](/img/in-post/docker-net/8.png)
![java-javascript](/img/in-post/docker-net/9.png)


### 附录

### 参考

[linux 路由表设置 之 route 指令详解](https://blog.csdn.net/vevenlcf/article/details/48026965)

[理解Docker单机容器网络](https://tonybai.com/2016/01/15/understanding-container-networking-on-single-host/)

[docker网络文档](https://docs.docker.com/network/)

[IPtables中SNAT、DNAT和MASQUERADE的含义](https://blog.csdn.net/jk110333/article/details/8229828)