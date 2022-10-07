---
layout:     post
title:      "drafts"
date:       2022-10-1 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - k8s
---

	kubeadm join 192.168.31.201:6443 --token a8yffb.6lzm9f9ltqlzlckw \
    --discovery-token-ca-cert-hash sha256:e81c3664790e9442e4b42c008c7c13f087227b9cae073cd50d1976c0ed1b4f81 
	
	
	  Ready            False   Thu, 06 Oct 2022 09:23:53 -0400   Thu, 06 Oct 2022 08:29:41 -0400   KubeletNotReady              runtime network not ready: NetworkReady=false reason:NetworkPluginNotReady message:docker: network plugin is not ready: cni config uninitialized
	  
	  
	  [decent@HostGW-Master ~]$ kubectl get nodes
NAME            STATUS     ROLES    AGE   VERSION
hostgw-master   NotReady   master   57m   v1.16.9
hostgw-node     NotReady   <none>   16m   v1.16.9


[decent@HostGW-Master ~]$ ip route
default via 192.168.31.1 dev ens33 proto static metric 100 
10.42.0.0/24 dev cni0 proto kernel scope link src 10.42.0.1 
172.17.0.0/16 dev docker0 proto kernel scope link src 172.17.0.1 
192.168.31.0/24 dev ens33 proto kernel scope link src 192.168.31.201 metric 100 

[decent@HostGW-Node ~]$ ip route
default via 192.168.31.1 dev ens33 proto static metric 100 
10.42.1.0/24 dev cni0 proto kernel scope link src 10.42.1.1 
172.17.0.0/16 dev docker0 proto kernel scope link src 172.17.0.1 
192.168.31.0/24 dev ens33 proto kernel scope link src 192.168.31.202 metric 100


[decent@HostGW-Master ~]$ kubectl get pods -o wide                              
NAME                                READY   STATUS    RESTARTS   AGE    IP           NODE            NOMINATED NODE   READINESS GATES
nginx-deployment-574b87c764-hssb7   1/1     Running   0          4m9s   10.42.1.95   hostgw-node     <none>           <none>
nginx-deployment-574b87c764-tpbtz   1/1     Running   0          4m9s   10.42.1.96   hostgw-node     <none>           <none>
nginx-deployment-574b87c764-x77km   1/1     Running   0          4m9s   10.42.0.6    hostgw-master   <none>           <none>


[decent@HostGW-Master ~]$ ping 10.42.1.96
PING 10.42.1.96 (10.42.1.96) 56(84) bytes of data.
^C
--- 10.42.1.96 ping statistics ---
3 packets transmitted, 0 received, 100% packet loss, time 2001ms

[decent@HostGW-Master ~]$ ping 10.42.0.6
PING 10.42.0.6 (10.42.0.6) 56(84) bytes of data.
64 bytes from 10.42.0.6: icmp_seq=1 ttl=64 time=0.063 ms
64 bytes from 10.42.0.6: icmp_seq=2 ttl=64 time=0.053 ms
^C
--- 10.42.0.6 ping statistics ---
2 packets transmitted, 2 received, 0% packet loss, time 1000ms
rtt min/avg/max/mdev = 0.053/0.058/0.063/0.005 ms


[decent@HostGW-Master ~]$ traceroute 10.42.1.97
traceroute to 10.42.1.97 (10.42.1.97), 30 hops max, 60 byte packets
 1  192.168.31.202 (192.168.31.202)  0.254 ms  0.260 ms  0.218 ms
 2  * * *
 3  * * *
 4  * * *
 5  * * *
 6  * * *
 7  *^C
 
 https://serverfault.com/questions/334029/what-does-mean-when-traceroute
 
 
 Chain FORWARD (policy DROP 13 packets, 585 bytes)
 pkts bytes target     prot opt in     out     source               destination         
10525  475K KUBE-FORWARD  all  --  any    any     anywhere             anywhere             /* kubernetes forwarding rules */
10525  475K KUBE-SERVICES  all  --  any    any     anywhere             anywhere             ctstate NEW /* kubernetes service portals */
10525  475K DOCKER-USER  all  --  any    any     anywhere             anywhere            
10525  475K DOCKER-ISOLATION-STAGE-1  all  --  any    any     anywhere             anywhere            
    0     0 ACCEPT     all  --  any    docker0  anywhere             anywhere             ctstate RELATED,ESTABLISHED
    0     0 DOCKER     all  --  any    docker0  anywhere             anywhere            
    0     0 ACCEPT     all  --  docker0 !docker0  anywhere             anywhere            
    0     0 ACCEPT     all  --  docker0 docker0  anywhere             anywhere 
	
	
	
Chain FORWARD (policy DROP 24 packets, 1080 bytes)
 pkts bytes target     prot opt in     out     source               destination         
10736  484K KUBE-FORWARD  all  --  any    any     anywhere             anywhere             /* kubernetes forwarding rules */
10736  484K KUBE-SERVICES  all  --  any    any     anywhere             anywhere             ctstate NEW /* kubernetes service portals */
10736  484K DOCKER-USER  all  --  any    any     anywhere             anywhere            
10736  484K DOCKER-ISOLATION-STAGE-1  all  --  any    any     anywhere             anywhere            
    0     0 ACCEPT     all  --  any    docker0  anywhere             anywhere             ctstate RELATED,ESTABLISHED
    0     0 DOCKER     all  --  any    docker0  anywhere             anywhere            
    0     0 ACCEPT     all  --  docker0 !docker0  anywhere             anywhere            
    0     0 ACCEPT     all  --  docker0 docker0  anywhere             anywhere  
	
	
	
	
	sudo iptables -L FORWARD -n -v --line-numbers
	# 删除 FORWARD 链 的第三条规则
	sudo iptables -D FORWARD 3
	
	
	sysctl -w net.bridge.bridge-nf-call-iptables=1

kubeadm join 192.168.31.201:6443 --token azd8x5.b9r3tm0zitodtvvx \
    --discovery-token-ca-cert-hash sha256:bc3eac1174d8e04ac7dd14ae160633938af94e5902547f69c9a536350d921907 
	
	
[decent@HostGW-Master ~]$ brctl show
bridge name     bridge id               STP enabled     interfaces
cni0            8000.c6b3d56dd265       no              veth7b6293ab
                                                        veth8b6dd897
                                                        vethed602629
docker0         8000.0242dd6af105       no


[decent@HostGW-Node tcpdump]$ brctl show
bridge name     bridge id               STP enabled     interfaces
cni0            8000.9e36e1265549       no              veth0cecb20e
                                                        veth7c0cbd24
docker0         8000.02424cc6a318       no


2: ens33: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc pfifo_fast state UP group default qlen 1000
    link/ether 00:50:56:2c:93:b5 brd ff:ff:ff:ff:ff:ff
    inet 192.168.31.201/24 brd 192.168.31.255 scope global noprefixroute ens33
       valid_lft forever preferred_lft forever
    inet6 fe80::49b6:6a50:bd62:d604/64 scope link noprefixroute 
       valid_lft forever preferred_lft forever
3: docker0: <NO-CARRIER,BROADCAST,MULTICAST,UP> mtu 1500 qdisc noqueue state DOWN group default 
    link/ether 02:42:dd:6a:f1:05 brd ff:ff:ff:ff:ff:ff
    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0
       valid_lft forever preferred_lft forever
4: cni0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1450 qdisc noqueue state UP group default qlen 1000
    link/ether c6:b3:d5:6d:d2:65 brd ff:ff:ff:ff:ff:ff
    inet 10.244.0.1/24 brd 10.244.0.255 scope global cni0
       valid_lft forever preferred_lft forever
    inet6 fe80::c4b3:d5ff:fe6d:d265/64 scope link 
       valid_lft forever preferred_lft forever
	   
	   
[decent@HostGW-Node tcpdump]$ ping -c 1 -I ens33 10.244.1.2
PING 10.244.1.2 (10.244.1.2) from 192.168.31.202 ens33: 56(84) bytes of data.

--- 10.244.1.2 ping statistics ---
1 packets transmitted, 0 received, 100% packet loss, time 0ms