---
layout:     post
title:      "restart卡主"
date:       2020-05-01 20:28:00
author:     "weak old dog"
header-img-credit: false
tags:
    - docker
---

使用下面yaml创建pod
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
使用`docker exec 259da1678e8c ps -ef`命令查看容器中进程，注意到18号进程有个父进程12。其中259da1678e8c为container1容器的id.
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
现在使用`docker restart a46c18158270`命令重启container2容器，命令卡主了，直到`sleep 1000`执行结束。其中a46c18158270为container2容器的id.
![java-javascript](/img/in-post/container_hang/containerd.png)

继续使用docker exec命令查看容器进程pid，发现`sleep 1000`进程被0号进程接收。
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

问题原因：在共享pid namespace情况下，containerd在kill容器时并没有kill init进程的子进程，sh进程被kill了，但是sleep进程还存在，导致容器无法退出

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