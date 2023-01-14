---
layout:     post
title:      "K8s 资源控制概述"
subtitle:   " \"cpu与mem的request与limit\""
date:       2020-1-16 10:06:00
author:     "weak old dog"
header-img-credit: false
tags:
    - K8s
---

这部分关注三个问题
* K8s 是如何对 cpu 和 mem 做资源限制的，涉及到的是 cgroup 的东西
* pod 层面的 cgroup 存在的意义，可能是除 container 以外的本 pod 使用的资源
* best effort/burstable/guaranteed 三者存在的意义

首先从观察现象出发，然后试图解释现象

分别介绍这三部分
### K8s是如何做资源限制的
这部分内容要区分mem与cpu，两者都使用cgroup，但是稍有区别，以下面pod为例，描述k8s限制资源使用时的现象。
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: frontend
spec:
  containers:
  - name: db
    image: mysql
    env:
    - name: MYSQL_ROOT_PASSWORD
      value: "password"
    resources:
      requests:
        memory: "64Mi"
        cpu: "250m"
      limits:
        memory: "128Mi"
        cpu: "500m"
```

##### 限制mem
在上面的yaml中，配置mem的request为64Mi，limit为128Mi，我们看生成的cgroup配置文件是啥。

启动pod，查看container中进程在主机上的`docker inspect -f '{{.State.Pid}}' 634fc2ea719b`，其中634fc2ea719b为container的id，输出为15132。

执行`cat /proc/15132/cgroup | grep memory`，查看此进程所在的cgroup，输出为：
```s
11:memory:/kubepods/burstable/pod31683904-39c5-11ea-a5bf-8cec4be843ab/634fc2ea719b6ed1688ddf4c4155db56f4d230639b82ef516ff8af37ee88cdb6
```
这几列的含义：cgroup的id，cgroup所绑定的subsystem，进程在 cgroup 树中的路径，即进程所属的 cgroup，这个路径是相对于挂载点的相对路径。cgroup的挂载点可以通过`mount | grep cgroup`看到，本机为/sys/fs/cgroup/，这个是系统默认的`hierarchy`，可以通过

`mount -t cgroup -o none,name=cgroup-test cgroup-test ./cgroup-test`

创建一个新的hierarchy，（一个subsystem只能附加到一个hierarchy上，**可以看一下，docker是如何查找subsystem子系统**）。

执行`cat memory.limit_in_bytes`，输出：134217728，这个值是`128Mi`，所以是我们的设置的limit值。`memory.max_usage_in_bytes`也是这个值。`memory.usage_in_bytes`表示cgroup 中进程当前使用的总内存字节，`memory.soft_limit_in_bytes`这个值为0。注意，我们在cgroup中发现mem被限制为`128Mi`也就是pod的yaml中定义的limit值，mem的request值并没有在cgroup中定义，request是在scheduler调度pod时，计算资源用的。

##### 限制cpu
限制cpu资源时，m表示千分之一核，也就是1 Core=1000m，因此该资源对象指定容器进程需要250/1000核，也就是一个核的1/4的时间片。当不指定m，也就是只输入1时，就表示一个核心。
查看进程cpu所属的group
```s
root@z-Latitude:resourceLimit# cat /proc/8976/cgroup | grep cpu
6:cpuset:/kubepods/burstable/pod959710a8-39cf-11ea-a5bf-8cec4be843ab/9f9e6fb02236a31bcb4394c9b0e6a2e0bcddd614a9a649eae0bb79e3b6a242f7
4:cpu,cpuacct:/kubepods/burstable/pod959710a8-39cf-11ea-a5bf-8cec4be843ab/9f9e6fb02236a31bcb4394c9b0e6a2e0bcddd614a9a649eae0bb79e3b6a242f7
```
下面几项的值为：

`cpu.shares`: 256

`cpu.cfs_period_us`: 100000

`cpu.cfs_quota_us`：50000

`cpu.shares`为cpu request的值，这个参数用来设置CPU的相对值，并且是针对所有cpu的，假如系统中有两个cgroup，分别是A和B，A的share是1024，B的share是512，那么A将获得1024/(1204+512)=66%的CPU资源，而B将获得33%的CPU资源。share有两个特点:
* 如果A不忙，没有使用到66%的CPU时间，那么剩余的CPU时间将会被系统分配给B，即B的CPU使用率可以超过33%。
* 如果添加了一个新的cgroup C，且它的shares值是1024，那么A的限额变成了1024/(1204+512+1024)=40%，B的变成了20%。

所以share是不能限制cpu使用的绝对值的。`cpu.cfs_period_us`是用来干这个的。cfs_period_us用来配置时间周期长度，cfs_quota_us用来配置当前cgroup在设置的周期长度内所能使用的CPU时间数，两个文件配合起来设置CPU的使用上限。我们在yaml中设置的是`500m`，从语义来说，是限制最多使用半个cpu。那么这里cfs_period_us就应该是cfs_quota_us的两倍，因为前者设置为100000，所以后者设置为50000.



### 参考
[CGroup 介绍、应用实例及原理描述](https://www.ibm.com/developerworks/cn/linux/1506_cgroup/index.html)

[深入理解K8s资源限制](https://qingwave.github.io/2019/01/09/%E6%B7%B1%E5%85%A5%E7%90%86%E8%A7%A3K8s%E8%B5%84%E6%BA%90%E9%99%90%E5%88%B6/#%E5%86%85%E5%AD%98%E9%99%90%E5%88%B6)

[Understanding resource limits in kubernetes: cpu time](https://medium.com/@betz.mark/understanding-resource-limits-in-kubernetes-cpu-time-9eff74d3161b)

[K8s文档：Managing Compute Resources for Containers](https://kubernetes.io/docs/concepts/configuration/manage-compute-resources-container/#how-pods-with-resource-limits-are-run)