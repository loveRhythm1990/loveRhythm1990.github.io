---
layout:     post
title:      "containerd runc docker之间的关系"
date:       2020-03-21 18:10:00
author:     "weak old dog"
header-img-credit: false
tags:
    - docker
---

从 Docker 1.11 开始，Docker 容器运行已经不是简单的通过 Docker daemon 来启动，而是集成了containerd、runC 等多个组件。Docker 服务启动之后，我们也可以看见系统上启动了 dockerd、docker-containerd 等进程，本文主要介绍新版 Docker（1.11 以后）每个部分的功能和作用。

#### runC
runc是由Docker公司的libcontainer项目发展来的，目前托管与OCI组织，runc是一个轻量级的容器运行时引擎，包括所有Docker使用的和容器相关的系统调用的代码，完全支持Linux Namespace，包括User Namespace，原生支持所有Linux提供的安全特性：SeLinux，pivot_root等

###### OCI 标准包（bundle）
一个标准的容器运行时需要文件系统，也就是镜像，OCI bundle包括两个模块：
* config.json包括容器的配置数据。
* 一个文件夹，代码容器的root文件系统，名字可以随便取，一般为rootfs

config.json包含容器必需的元信息，主要包括容器需要去运行的进程、环境变量、沙盒环境等，下面简单介绍：
* root: 配置容器的root文件系统，也就是上面的OCI 文件系统，可以是绝对路径，也可以是相对路径。
* mounts: 挂载相关信息，包括在容器中的路径，文件系统类型等
* process: 用来配置容器进程，包括运行的参数，环境变量等
* user: 用户信息

还有其他信息，暂时不讨论。

###### 利用runc启动container的例子
首先要有一个busybox的镜像，利用这个镜像创建一个container，然后利用docker export命令将这个container的rootfs归档，然后解压到一个rootfs目录：
```s
$ docker pull busybox
$ mkdir -p /tmp/mycontainer/rootfs
$ cd /tmp/mycontainer
$ docker export $(docker create busybox) | tar -C rootfs -xvf -
```
然后这个rootfs目录就是busybox镜像的文件系统，我们还需要生成一个`config.json`配置文件
```s
$ runc spec
```
此时目录结构大概如下：
```s
root@z-Latitude:mycontainer# ls
config.json  rootfs
root@z-Latitude:rootfs# ls rootfs/
bin  dev  etc  home  proc  root  sys  tmp  usr  var
```
现在可以使用`runc run mybusybox`来运行容器了，由此看来，我们使用runc运行容器，需要一个`config.json`配置文件，需要一个rootfs目录。

#### containerd
contaienrd并不是直接面向最终用户的，而是主要集成到更上层的系统，比如k8s/mesos等容器编排系统。containerd以daemon的形式运行在系统上，通过unix domain socket暴露底层的gRPC API，上层系统可以通过这些API管理机器上的容器。每个containerd只负责一台机器，Pull镜像、对容器的操作（启动、停止等）、网络、存储都是由containerd完成的。具体运行容器由runC负责，实际上只要是符合OCI规范的容器都可以支持。

containerd在拉取镜像之后，处理OCI格式，由runc去启动容器。

###### containerd和docker之间的关系
Docker包含containerd，containerd专注于运行时容器管理，不包含容器的构建操作，这部分是由docker完成的。containerd提供的API偏底层，不是给普通用户使用的，主要集成于上层应用。docker daemon为dockerd，其调用containerd完成镜像和容器的相关工作，和containerd的通信socket文件为docker-containerd.sock

OCI是一个标准化的容器规范，包括运行时规范和镜像规范，runC是基于此规范的一个实现。从技术栈上看，containerd比runC的层次更高，containerd可以使用runc容器，还以下载镜像，管理网络等。参考下图，（图片来自[从 docker 到 runC](https://www.cnblogs.com/sparkdev/p/9129334.html)）
![java-javascript](/img/in-post/containerd/2.png)

另外这里[Use of containerd-shim in docker-architecture](https://groups.google.com/forum/#!topic/docker-dev/zaZFlvIx1_k)解释了`containerd-shim`存在的原因：
> The shim allows for daemonless containers.  It basically sits as the parent of the container's process to facilitate a few things. 
>  
> First it allows the runtimes, i.e. runc,to exit after it starts the container.  This way we don't have to have the long running runtime processes for containers.  When you start mysql you should only see the mysql process and the shim.  
>
> Second it keeps the STDIO and other fds open for the container incase containerd and/or docker both die.  If the shim was not running then the parent side of the pipes or the TTY master would be closed and the container would exit.  
> 
> Finally it allows the container's exit status to be reported back to a higher level tool like docker without having the be the actual parent of the container's process and do a wait4.  
> 
> I did a talk on this last week at dockercon US.  You can see my slides here.  https://github.com/crosbymichael/dockercon-2016

#### 关于CRI
CRI是Container Runtime Interface，容器运行时接口，是一组接口规范，这一规范让Kubernetes无需重新编译就能使用更多的容器运行时。主要包括两组接口：RuntimeService以及ImageService

kubelet通过gRPC框架与CRI shim进行通信，CRI shim通过Unix socket启动一个gRPC server提供容器运行时服务。kubelet作为一个gRPC client，CRI shim作为一个server，比较常用的CRI shim有docker-shim/containerd-shim/cri-o-shim。


#### 参考

[自己动手写docker]()

[从 docker 到 runC](https://www.cnblogs.com/sparkdev/p/9129334.html)

[Runc简介](https://www.cnblogs.com/sparkdev/p/9032209.html)

[浙大SEL实验室-深入理解Docker容器引擎runC执行框架](http://www.sel.zju.edu.cn/?p=840)
