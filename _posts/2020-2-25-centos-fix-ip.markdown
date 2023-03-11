---
layout:     post
title:      "centos 配置静态 ip"
date:       2020-2-25 15:55:00
author:     "weak old dog"
header-img-credit: false
tags:
    - 能效
---

家里有个 windows 台式机，安装了 vmware，通过 vmware 虚拟机搭集群测试时，是不能用 dhcp 的，每次虚拟机重启后，节点 ip 地址变化了，那 K8s 集群就废了。

这里记录下怎么修改 centos ip，并配置为静态 ip。网卡的配置文件在 `/etc/sysconfig/network-scripts` 目录：
```s
[decent@HostGW-Master ~]$ ls /etc/sysconfig/network-scripts
ifcfg-ens33  ifdown-eth   ifdown-post    ifdown-Team      ifup-aliases  ifup-ipv6   ifup-post    ifup-Team      init.ipv6-global
```
比如上面的 ifcfg-ens33 目录。

修改文件的内容如下，重点是 `static` 以及后面的四行，这个 ip 地址是可以自己配置的。网关和 dns 为什么填 `192.168.31.1`? 因为我看自己的 mac 电脑是这么设置的，这个可能跟家里的路由器有关。
```s
[decent@HostGW-Master ~]$ cat /etc/sysconfig/network-scripts/ifcfg-ens33
TYPE=Ethernet
PROXY_METHOD=none
BROWSER_ONLY=no
BOOTPROTO=static
DEFROUTE=yes
IPV4_FAILURE_FATAL=no
IPV6INIT=yes
IPV6_AUTOCONF=yes
IPV6_DEFROUTE=yes
IPV6_FAILURE_FATAL=no
IPV6_ADDR_GEN_MODE=stable-privacy
NAME=ens33
UUID=53ee1928-b75a-417f-b1d2-dcfcc33abee3
DEVICE=ens33
ONBOOT=yes
IPADDR=192.168.31.201
GATEWAY=192.168.31.1
NETMASK=255.255.255.0
DNS1=192.168.31.1
```

还有几点，虚拟机的网络模式要设置成桥接模式，这样通过 mac 就能 ssh 到 windows 中的虚拟机。 