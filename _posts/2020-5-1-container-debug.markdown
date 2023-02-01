---
layout:     post
title:      "容器share pid 导致 restart 命令卡主"
date:       2020-05-01 20:28:00
author:     "weak old dog"
header-img-credit: false
tags:
    - Docker
---

在工作中发现执行 `docker restart` 命令重启一个容器的时候，容器卡主了，这里分析总结下原因。其结论是容器 share pid namespace 功能不完善，社区已经有 PR 修复问题。

### 问题复现/现象
使用下面 yaml 创建 pod，在 yaml 中配置了两个容器，并且设置了共享 pid namespace：`shareProcessNamespace: true`
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: nginx-jc
spec:
  shareProcessNamespace: true
  containers:
  - command: ["/bin/sh", "-c", "sleep 12345"]
    name: container1
    image: ubuntu
    ports:
    - containerPort: 80
    imagePullPolicy: IfNotPresent
  - command: ["/bin/sh", "-c", "touch /tmp/healthy && sleep 1000"]
    name: container2
    image: ubuntu
    imagePullPolicy: IfNotPresent
```
使用 `docker exec 259da1678e8c ps -ef` 命令查看容器 container1 中进程（259da1678e8c 为第一个容器的容器 id），注意到 18 号进程有个父进程 12。
```s
root@ubuntu:~# docker exec 259da1678e8c ps -ef
UID         PID   PPID  C STIME TTY          TIME CMD
root          1      0  0 01:39 ?        00:00:00 /pause
root          6      0  0 01:39 ?        00:00:00 /bin/sh -c sleep 12345
root         11      6  0 01:39 ?        00:00:00 sleep 12345
root         12      0  0 01:39 ?        00:00:00 /bin/sh -c touch /tmp/healthy && sleep 1000
root         18     12  0 01:39 ?        00:00:00 sleep 1000
root         19      0  0 01:41 ?        00:00:00 ps -ef
```
现在使用 `docker restart a46c18158270` 命令重启 container2 容器（a46c18158270 是第二个容器的 id），命令卡主了，直到`sleep 1000`执行结束。.

继续使用 docker exec 命令查看容器 container1 进程 pid，发现 `sleep 1000` 进程被0号进程接收。
```s
root@ubuntu:~# docker exec 259da1678e8c ps -ef
UID         PID   PPID  C STIME TTY          TIME CMD
root          1      0  0 01:39 ?        00:00:00 /pause
root          6      0  0 01:39 ?        00:00:00 /bin/sh -c sleep 12345
root         11      6  0 01:39 ?        00:00:00 sleep 12345
root         18      0  0 01:39 ?        00:00:00 sleep 1000
root         35      0  0 01:49 ?        00:00:00 ps -ef
```
此时使用docker ps命令，仍然能看到容器存在：
```s
root@ubuntu:~# docker ps | grep touch
a46c18158270        1d622ef86b13           "/bin/sh -c 'touch /…"   24 minutes ago      Up 7 minutes                            k8s_container2_nginx-jc_default_bfb31ed1-8c15-11ea-b37d-000c29df44e0_0
```
使用下面命令查看容器状态时，容器仍然会卡主，并且docker日志没有任何输出。
```s
root@ubuntu:~# docker inspect a46c18158270
^C
root@ubuntu:~# docker logs a46c18158270
^C
```

### 问题原因
问题原因：在共享 pid namespace 情况下，containerd 在 kill 容器时并没有 kill init 进程的子进程，sh 进程被 kill了，但是 sleep 进程还存在，导致容器无法退出

相关issue和pr为：

[docker cannot terminate a container when processes linger after TERM](https://github.com/moby/moby/issues/38978)

[fix killall when use pidnamespace](https://github.com/containerd/containerd/pull/3149)

这个bug是由下面这个问题引入的，之前的containerd没有此问题。
[Do not KillAll on task delete by default](https://github.com/containerd/containerd/pull/2597)

打印docker的goroutine stack：使用下面命令向dockerd进程发送SIGUSR1信号，会在`/var/run/docker`目录下面生成docker的goroutine stack，比如：`goroutine-stacks-2020-04-30T213111-0700.log`
```s
root@ubuntu:/var/run/docker# ps -ef | grep dockerd
root        836      1  1 May01 ?        00:05:00 /usr/bin/dockerd -H fd:// --containerd=/run/containerd/containerd.sock

root@ubuntu: kill -10 836
```
打印containerd的goroutine stack：同样是向containerd进程发送SIGUSR1信号：
```s
root@ubuntu:/var/log# ps -ef | grep containerd
root        831      1  0 May01 ?        00:00:34 /usr/bin/containerd

root@ubuntu: kill -10 831
```
在linux中，containerd默认用systemd管理，containerd的stack dump默认打印在了日志中。因此可以使用`journalctl -u containerd`来查看container的goroutine stack.

参考：
[How to dump goroutines' stacktraces](https://success.docker.com/article/how-to-dump-goroutines-stacktraces)
下面issue修的是同样的问题。
[Fix kill when shared pid namespace.](https://github.com/containerd/cri/pull/983)