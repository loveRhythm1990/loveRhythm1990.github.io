---
layout:     post
title:      "通过修改config文件的方式修改容器启动参数"
date:       2022-10-24 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - 运维
    - docker
---

有时候在系统中，有一些服务是直接通过容器启动的，比如 rancher 系统中的 K8s master 组件。如果要修改这些容器的参数，需要通过集群运维工具 rke 重新升级一下集群，这个代价比较大，这里有个 trick 的方式是直接修改容器的 config 文件。以 kubelet 容器为例步骤如下

1. 停止容器
```s
docker stop kubelet
```

2. 找到容器的配置文件，其路径为 `/var/lib/docker/containers/<container-id>/config.v2.json`，容器路径最好从 docker inspect 的输出中复制。

3. 在配置文件中，修改特定的配置。这里我们假设要将日志级别调整为 3，修改之前的日志级别为 2，注意修改这个文件一般需要 root 权限
```s
## 修改之前
"--streaming-connection-idle-timeout=30m",
"--fail-swap-on=false",
"--resolv-conf=/etc/resolv.conf",
"--client-ca-file=/etc/kubernetes/ssl/kube-ca.pem",
"--v=2"
```

4. 重启 docker，【这里测试了一下，只重启容器是不行的，而且这里不用启动 kubelet，使用 `systemctl restart docker` 重启 docker 的时候，原来 stop 的容器会自己启动】
```s
## kubelet 参数修改之后
"--fail-swap-on=false",
"--resolv-conf=/etc/resolv.conf",
"--client-ca-file=/etc/kubernetes/ssl/kube-ca.pem",
"--pod-infra-container-image=10.73.245.148:5000/mcloud/pause:3.1",
"--v=3"
```


参考[Docker – change container configuration in 4 ways](https://bobcares.com/blog/docker-change-container-configuration/)


