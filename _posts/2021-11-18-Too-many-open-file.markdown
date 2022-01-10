---
layout:     post
title:      "too many open files 问题排查"
date:       2021-11-18 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - linux
---

经常在集群中遇到 `too many open files` 问题，整理下原因与配置
### 相关现象

#### 进程日志
问题发生时的 etcd 日志：
```sh
2021-12-27 08:49:13.292514 I | embed: rejected connection from "172.16.16.22:41266" (error "open /etc/kubernetes/ssl/kube-etcd-172-16-16-26.pem: too many open files in system", ServerName "")

2021-12-27 08:49:13.294184 C | etcdmain: accept tcp [::]:2380: accept4: too many open files in system
```
#### 系统日志
查看 /var/log/messages
```sh
Dec 27 16:48:46 hostname kernel: VFS: file-max limit 65536 reached
```

### 配置最大fd
#### 系统层面最大fd
##### 配置
在 `/etc/sysctl.conf` 文件中，添加下面配置：
```sh
fs.file-max = 100000
```
运行 `sysctl -p` 使其生效

##### 查看
当前系统配置的最大值
```sh
cat /proc/sys/fs/file-max
```
当前系统已经使用的 fd，最左侧为已经使用的，右侧为最大值。
```sh
cat /proc/sys/fs/file-nr
```
#### 进程层面配置
##### 查看
使用 ulimit -a 命令查看。关注 “open files” 字段
##### 临时配置
使用 ulimit -n 命令配置最大可打开的文件数。

##### 持久化配置
修改 `/etc/security/limits.conf` 文件，需要重启
```sh
# /etc/security/limits.conf
* soft nofile 65535
* hard nofile 65535
```
#### docker 配置
* 给整个 docker 层面配置：在 docker.service 配置文件中配置。
配置 `LimitNOFILE=1048576`，
* 给单个容器配置：在运行容器时，指定 --ulimit 选项。
```sh
docker run --ulimit nofile=1024:1024 --rm debian sh -c "ulimit -n" 1024
```
### 查看进程使用的fd
#### 在宿主机查看
```sh
ls -l /proc/[pid]/fd

lsof -p [pid]
```
#### 容器内部
这个只是针对网络来说，对于网络来说，宿主机上看不到容器内的网络连接信息。具体表现为：

* netstat: 看不到容器内部的连接信息，不是一个网络 namespace

* ls -l /proc/[pid]/fd 以及 lsof：只能看到有 socket 连接，但是连接状态以及 Local Address 和 Foreign Address 看不到。

这个时候需要进入到容器的网络 namespace 中查看，步骤如下：

通过这个容器的 pause 容器看到这个容器的 net namespace:

使用 nsenter 进入这个容器的 net namespace，然后通过 netstat 等命令查看

```sh
sudo nsenter --net=/var/run/docker/netns/13ea8a963fbf 
```
### 参考

[如何诊断 'TOO MANY OPEN FILES' 问题？](https://www.ibm.com/support/pages/%E5%A6%82%E4%BD%95%E8%AF%8A%E6%96%AD-too-many-open-files-%E9%97%AE%E9%A2%98%EF%BC%9F)

[Set ulimits in container (--ulimit)](https://docs.docker.com/engine/reference/commandline/run/#set-ulimits-in-container---ulimit)

[Is it possible to configure openfiles limit for docker containers ?](https://access.redhat.com/solutions/3361091)

