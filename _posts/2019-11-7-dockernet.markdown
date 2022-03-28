---
layout:     post
title:      "Linux IPtables 规则基础"
date:       2019-11-7 8:24:00
author:     "weak old dog"
header-img-credit: false
tags:
    - 网络
---

IPtables 这个东西每次都花时间去看，然而真正排查问题的时候，每次都不懂了，关键是实践总结的太少，纸上得来终觉浅，绝知此事要躬行。本文主要参考《Kubernetes 网络权威指南》。

### IPtables
iptables 的底层实现是 netfilter，netfilter 是Linux内核2.4版引入的一个子系统，它作为一个通用的、抽象的框架，提供一整套 hook 函数的管理机制，使得数据包过滤、包处理（设置标志位、修改TTL等）、地址伪装、网络地址转换、透明代理、访问控制、基于协议类型的连接跟踪，甚至带宽限速等功能成为可能。netfilter 的架构就是在整个网络流程的若干位置放置一些钩子，并在每个钩子上挂载一些处理函数进行处理。

IP层的5个钩子点的位置，对应iptables就是5条内置链，分别是PREROUTING、POSTROUTING、INPUT、OUTPUT和FORWARD。netfilter原理图如图，在下面图中，也列出了每个内置链中所具有的表，
![](/img/in-post/all-in-one/2022-03-27-22-46-49.png){:height="80%" width="80%"}
当一个报文到达主机时，其处理流程大概如下：

1. 当网卡上收到一个包送达协议栈时，最先经过的 netfilter 钩子是 PREROUTING，如果确实有用户埋了这个钩子函数，那么内核将在这里对数据包进行目的地址转换（DNAT）。不管在 PREROUTING 有没有做过 DNAT，内核都会通过查本地路由表决定这个数据包是发送给本地进程还是发送给其他机器。
> 这里需要注意，查路由表（也就是上面的 `ROUTE`）是在 PREROUTING 链之后的。

2. 如果是发送给其他机器（或其他 network namespace），就相当于把本地当作路由器，就会经过 netfilter 的FORWARD 钩子，用户可以在此处设置包过滤钩子函数，例如 iptables 的 reject 函数。
3. 所有马上要发到协议栈外的包都会经过 POSTROUTING 钩子，用户可以在这里埋下源地址转换（SNAT）或源地址伪装（Masquerade，简称Masq）的钩子函数。
4. 如果经过上面的路由决策，内核决定把包发给本地进程，就会经过 INPUT 钩子。本地进程收到数据包后，回程报文会先经过 OUTPUT 钩子，然后经过一次路由决策（例如，决定从机器的哪块网卡出去，下一跳地址是多少等），最后出协议栈的网络包同样会经过 POSTROUTING 钩子。

上面 PREROUTING 是在路由之前，POSTROUTING 是在路由之后，前者主要用于 DNAT，后者主要用于 SNAT(Masq)。

### table、chain、rule
iptables 是用户空间的一个程序，通过 netlink 和内核的 netfilter 框架打交道，负责往钩子上配置回调函数。一般情况下用于构建 Linux 内核防火墙，特殊情况下也做服务负载均衡（Kubernetes 操作）。iptables 的工作原理如图所示（果然是工作在 IP 层）。
![](/img/in-post/all-in-one/2022-03-27-14-30-18.png){:height="50%" width="50%"}
我们常说的iptables 5X5，即5张表（table）和5条链（chain）。5条链即 iptables 的5条内置链，对应上文介绍的netfilter 的5个钩子。这5条链分别是：
* INPUT链：一般用于处理输入**本地进程**的数据包；
* OUTPUT链：一般用于处理**本地进程**的输出数据包；
* FORWARD链：一般用于处理**转发**到其他机器/network namespace的数据包；
* PREROUTING链：可以在此处进行 **DNAT**；
* POSTROUTING链：可以在此处进行 **SNAT**。

5张表如下所示，这5张表的优先级从高到低是：raw、mangle、nat、filter、security。需要注意的是，**iptables不支持用户自定义表**。
* filter表：用于控制到达某条链上的数据包是继续放行、直接丢弃（drop）或拒绝（reject）；
* nat表：用于修改数据包的源和目的地址；
* mangle表：用于修改数据包的IP头信息；
* raw表：iptables是有状态的，即iptables对数据包有连接追踪（connection tracking）机制，而raw是用来去除这种追踪机制的；
* security表：最不常用的表（通常，我们说iptables只有4张表，security表是新加入的特性），用于在数据包上应用SELinux。

不是每个链上都能挂表，本文第一张图，显示了每个链上的 table。

iptables 的规则是用户真正要书写的规则（规则就是挂在 netfilter 钩子上的函数，用来修改数据包的内容或过滤数据包）。一般情况下，一条 iptables 规则包含两部分信息：**匹配条件**和**动作**。匹配条件很好理解，即匹配数据包被这条 iptables 规则“捕获”的条件，例如协议类型、源IP、目的IP、源端口、目的端口、连接状态等。每条iptables规则允许多个匹配条件任意组合，从而实现多条件的匹配，多条件之间是逻辑与（&&）关系。
那么，数据包匹配后该怎么办，常见的动作有下面几个：
* DROP：直接将数据包丢弃，不再进行后续的处理。应用场景是不让某个数据源意识到你的系统的存在，可以用来模拟宕机；
* REJECT：给客户端返回一个connection refused或destination unreachable报文。应用场景是不让某个数据源访问你的系统，善意地告诉他：我这里没有你要的服务内容；
* QUEUE：将数据包放入用户空间的队列，供用户空间的程序处理；
* RETURN：跳出当前链，该链里后续的规则不再执行；
* ACCEPT：同意数据包通过，继续执行后续的规则；
* JUMP：跳转到其他用户自定义的链继续执行。

值得一提的是，用户自定义链中的规则和系统预定义的5条链里的规则没有区别。由于自定义的链没有与 netfilter 里的钩子进行绑定，所以它不会自动触发，**只能从其他链的规则中跳转过来，这也是JUMP动作存在的意义**。
在初步认识了iptables的表、链和规则三个最重要的概念后，我们介绍iptables命令的常见用法。


### 常用命令

1) 查看所有 iptables 规则（默认是 filter 表）： `iptables -L -n`，-n 表示选项将以数字形式列出信息，即将域名解析成 IP 地址。想输出更详细的信息，例如，经过某条 rule 处理的的数据包字节、进出网口等信息可以使用 `-v` 选项。

```s
[decent@node1 ~]$ sudo iptables -L -n
Chain INPUT (policy ACCEPT)
target     prot opt source               destination         
KUBE-EXTERNAL-SERVICES  all  --  0.0.0.0/0            0.0.0.0/0            ctstate NEW /* kubernetes externally-visible service portals */
KUBE-FIREWALL  all  --  0.0.0.0/0            0.0.0.0/0           

Chain FORWARD (policy DROP)
target     prot opt source               destination         
KUBE-FORWARD  all  --  0.0.0.0/0            0.0.0.0/0            /* kubernetes forwarding rules */
KUBE-SERVICES  all  --  0.0.0.0/0            0.0.0.0/0            ctstate NEW /* kubernetes service portals */
KUBE-EXTERNAL-SERVICES  all  --  0.0.0.0/0            0.0.0.0/0            ctstate NEW /* kubernetes externally-visible service portals */
DOCKER-USER  all  --  0.0.0.0/0            0.0.0.0/0           
DOCKER-ISOLATION-STAGE-1  all  --  0.0.0.0/0            0.0.0.0/0           
ACCEPT     all  --  0.0.0.0/0            0.0.0.0/0            ctstate RELATED,ESTABLISHED
DOCKER     all  --  0.0.0.0/0            0.0.0.0/0           
ACCEPT     all  --  0.0.0.0/0            0.0.0.0/0           
ACCEPT     all  --  0.0.0.0/0            0.0.0.0/0           
ACCEPT     all  --  10.42.0.0/16         0.0.0.0/0           
ACCEPT     all  --  0.0.0.0/0            10.42.0.0/16        

Chain OUTPUT (policy ACCEPT)
target     prot opt source               destination         
KUBE-SERVICES  all  --  0.0.0.0/0            0.0.0.0/0            ctstate NEW /* kubernetes service portals */
KUBE-FIREWALL  all  --  0.0.0.0/0            0.0.0.0/0           
   

Chain DOCKER-ISOLATION-STAGE-1 (1 references)
target     prot opt source               destination         
DOCKER-ISOLATION-STAGE-2  all  --  0.0.0.0/0            0.0.0.0/0           
RETURN     all  --  0.0.0.0/0            0.0.0.0/0           

Chain DOCKER-ISOLATION-STAGE-2 (1 references)
target     prot opt source               destination         
DROP       all  --  0.0.0.0/0            0.0.0.0/0           
RETURN     all  --  0.0.0.0/0            0.0.0.0/0           

Chain DOCKER-USER (1 references)
target     prot opt source               destination         
RETURN     all  --  0.0.0.0/0            0.0.0.0/0              

Chain KUBE-FIREWALL (2 references)
target     prot opt source               destination         
DROP       all  --  0.0.0.0/0            0.0.0.0/0            /* kubernetes firewall for dropping marked packets */ mark match 0x8000/0x8000
DROP       all  -- !127.0.0.0/8          127.0.0.0/8          /* block incoming localnet connections */ ! ctstate RELATED,ESTABLISHED,DNAT

Chain KUBE-FORWARD (1 references)
target     prot opt source               destination         
DROP       all  --  0.0.0.0/0            0.0.0.0/0            ctstate INVALID
ACCEPT     all  --  0.0.0.0/0            0.0.0.0/0            /* kubernetes forwarding rules */ mark match 0x4000/0x4000
ACCEPT     all  --  0.0.0.0/0            0.0.0.0/0            /* kubernetes forwarding conntrack pod source rule */ ctstate RELATED,ESTABLISHED
ACCEPT     all  --  0.0.0.0/0            0.0.0.0/0            /* kubernetes forwarding conntrack pod destination rule */ ctstate RELATED,ESTABLISHED
```

用户自己定义的链后面都有一个引用计数（如上面的`2 references`），在我们的例子中 KUBE-FIREWALL 和KUBE-FORWARD 都有一个引用，它们分别在 INPUT 和 FORWARD 中被引用。iptables 的每条链下面的规则处理顺序是从上到下逐条遍历的，除非中途碰到DROP，REJECT，RETURN这些内置动作。如果iptables规则前面是自定义链，则意味着这条规则的动作是JUMP，即跳到这条自定义链遍历其下的所有规则，然后跳回来遍历原来那条链后面的规则。以上过程如下图所示。

![](/img/in-post/all-in-one/2022-03-27-15-26-43.png){:height="40%" width="40%"}

2) 配置内置链的默认策略

```s
# 默认不让进
iptables --policy INPUT DROP
# 默认不转发
iptables --policy FORWARD DROP
# 默认可以出去
iptables --policy OUTPUT ACCEPT
```

3) 配置防火墙规则策略
防火墙策略一般分为通和不通两种。如果默认策略是"全通"，例如上文的 policy ACCEPT，就要定义一些策略来**封堵**；反之，如果默认策略是"全不通"，例如上文的 policy DROP，就要定义一些策略来**解封**。如果是做访问控制列表（ACL），即俗称的白名单，则用解封策略更常用。我们将用几个实际的例子来说明。

3.0) 查看某个表中，某个链上的规则，比如查看 nat 表的 KUBE-SVC-Y5VDFIEGM3DY2PZE 链，也就是 Kube-proxy 生成的负载均衡链。上面有两个规则，一个是 50% 的概率 jump 到 KUBE-SEP-IFV44I3EMZAL3LH3 链。一个是 jump 到 KUBE-SEP-6PNQETFAD2JPG53P 链。
```s
[decent@node1 ~]$ iptables -nvL KUBE-SVC-Y5VDFIEGM3DY2PZE -t nat
pkts  bytes target                     prot opt in     out     source               destination
0     0     KUBE-SEP-IFV44I3EMZAL3LH3  all  --  *      *       0.0.0.0/0            0.0.0.0/0            /* default/nginx-svc:80 */ statistic mode random probability 0.50000000000
1    60     KUBE-SEP-6PNQETFAD2JPG53P  all  --  *      *       0.0.0.0/0            0.0.0.0/0            /* default/nginx-svc:80 */
```
通过这个命令也可以理解 iptable 中：`表 -> 链 -> 规则` 的逻辑。一个链上可以有多个规则（上面是有两个），每个规则是顺序匹配的。一个链上的规则匹配完了之后，返回上一个链继续匹配。

3.1) 允许配置 SSH 连接，（默认配置的是 filter 表）
```s
iptables -A INPUT -s 10.20.30.40/24 -p tcp --dport 22 -j ACCEPT
```

简单分析这条命令，`-A`的意思是以追加（Append）的方式增加这条规则。`-A INPUT`表示这条规则挂在 INPUT 链上。`-s 10.20.30.40/24`表示允许源（source）地址是 10.20.30.40/24 这个网段的连接。`-p tcp`表示允许 TCP（protocol）包通过。`--dport 22` 的意思是允许访问的目的端口（destination port）为 22，即SSH端口。`-j ACCEPT`表示接受这样的连接。综上所述，这条iptables规则的意思是允许源地址是10.20.30.40/24这个网段的包发到本地TCP 22端口。除了按追加的方式添加规则，还可以使用`iptables -I［chain］［number］`将规则插入（Insert）链的指定位置。如果不指定number，则插到链的第一条处。

```s
# 查看 nat 表的 INPUT 链
[decent@node1 ~]$ sudo iptables -t nat -L INPUT 
Chain INPUT (policy ACCEPT)
target     prot opt source               destination    
# 查看 filter 表的 INPUT 链     
[decent@node1 ~]$ sudo iptables -t filter -L INPUT      
Chain INPUT (policy ACCEPT)
target     prot opt source               destination         
KUBE-EXTERNAL-SERVICES  all  --  anywhere             anywhere             ctstate NEW /* kubernetes externally-visible service portals */
KUBE-FIREWALL  all  --  anywhere             anywhere            
# 查看 mangle 表的 INPUT 链
[decent@node1 ~]$ sudo iptables -t mangle -L INPUT      
Chain INPUT (policy ACCEPT)
target     prot opt source               destination 
# 设置 INPUT 规则       
[decent@node1 ~]$ sudo iptables -A INPUT -s 10.20.30.40/24 -p tcp --dport 22 -j ACCEPT
# 再次确认规则
[decent@node1 ~]$ sudo iptables -t nat -L INPUT                                       
Chain INPUT (policy ACCEPT)
target     prot opt source               destination         
[decent@node1 ~]$ sudo iptables -t filter -L INPUT                                    
Chain INPUT (policy ACCEPT)
target     prot opt source               destination         
KUBE-EXTERNAL-SERVICES  all  --  anywhere             anywhere             ctstate NEW /* kubernetes externally-visible service portals */
KUBE-FIREWALL  all  --  anywhere             anywhere            
ACCEPT     tcp  --  10.20.30.0/24        anywhere             tcp dpt:ssh
[decent@node1 ~]$ sudo iptables -t mangle -L INPUT                                    
Chain INPUT (policy ACCEPT)
target     prot opt source               destination         
[decent@node1 ~]$ sudo iptables -t nat -A INPUT -s 10.20.30.40/24 -p tcp --dport 22 -j ACCEPT          
[decent@node1 ~]$ sudo iptables -t nat -L INPUT   
Chain INPUT (policy ACCEPT)
target     prot opt source               destination         
ACCEPT     tcp  --  10.20.30.0/24        anywhere             tcp dpt:ssh
```

3.2) 阻止来自某个 IP/网段的所有连接，比如阻止 10.10.10.10 上所有的包

```s
iptables -A INPUT -s 10.10.10.10 -j DROP
```
也可以使用`-j REJECT`，这样就会发一个连接拒绝的回程报文，客户端收到后立刻结束。不像-j DROP那样不返回任何响应，客户端只能一直等待直到请求超时。如果要屏蔽一个网段，例如10.10.10.0/24，则可以使用`-s 10.10.10.0/24`或10.10.10.0/255.255.255.0。如果要“闭关锁国”，即屏蔽所有的外来包，则可以使用`-s 0.0.0.0/0`。

3.3) 阻止外部连接访问本地 1234 端口，就需要在 INPUT 链挂规则

```s
iptables -A INPUT -p tcp --dport 1234 -j DROP
```

3.4) 端口转发，有时我们要把服务器的某个端口流量转发给另一个端口。例如，我们对外声称Web服务在 80 端口上运行，但由于种种原因 80 端口被人占了，实际的Web服务监听在 8080 端口上。为了让外部客户端能无感知地依旧访问80端口，可以使用以下命令实现端口转发：

```s
iptables -t nat -A PREROUTING -i eth0 -p tcp --dport 80 -j REDIRECT --to-port 8080
```
需要注意的是，我们修改了目的端口，因此该规则应该发生在 `nat` 表上的 `PREROUTING` 链上。至于 `-i eth0` 则表示匹配 eth0 网卡上接收到的包.

3.5) 地址欺骗（SNAP），神秘的网络地址欺骗其实是`SNAT`的一种。`SNAT`根据指定条件`修改数据包的源IP地址`，即DNAT 的逆操作。与 DNAT 的限制类似，SNAT 策略**只能发生在 nat 表的 POSTROUTING 链**。具体命令如下：

```s
iptables -t nat -A POSTROUTING -s 10.8.0.0/16 -j MASQUERADE
```
与SNAT类似，如果要控制被替换的源地址，则可以指定匹配从哪块网卡发出报文。例如：`-o eth0`选项指定报文从eth0出去并使用eth0的IP地址做源地址伪装。

3.6) 保存与恢复
* iptables-save -- dump 已配置的规则，可以用 > 重定向到一个文件中
* iptables-restore -- 从之前导出的iptable规则配置文件加载规则。

### 参考

[linux 路由表设置 之 route 指令详解](https://blog.csdn.net/vevenlcf/article/details/48026965)

[理解Docker单机容器网络](https://tonybai.com/2016/01/15/understanding-container-networking-on-single-host/)

[docker网络文档](https://docs.docker.com/network/)

[IPtables中SNAT、DNAT和MASQUERADE的含义](https://blog.csdn.net/jk110333/article/details/8229828)

[kube-proxy iptables规则分析](http://kuring.me/post/kube-proxy-iptables/)