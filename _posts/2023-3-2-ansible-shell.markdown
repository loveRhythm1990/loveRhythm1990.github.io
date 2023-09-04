---
layout:     post
title:      "K8s Shell 编程[TODO]"
date:       2023-3-2 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - 能效
    - TODO
---

**文章目录**
- [Mac as Proxy](#mac-as-proxy)
- [自动化工具集](#自动化工具集)

### Mac as Proxy
在测试过程中，发现镜像在虚拟机中很难拉下来，但是在mac 上却能拉下来，通过下面命令在 mac 上拉镜像，并推送到虚拟机上，下面脚本中使用了 ansible，得提前配置免密登录。
```s
#!/bin/sh
set -x

docker pull $1
docker save $1 -o "tmp.tar"

ansible vms -i ./hosts -m copy -a "src=./tmp.tar dest=/tmp/tmp.tar"
ansible vms -i ./hosts -m command -a "docker load -i /tmp/tmp.tar"
```
其中当前目录的 `hosts` 配置文件为，有个 vms 分组和三个虚拟机 ip。
```s
[vms]
192.168.31.201
192.168.31.202
192.168.31.203
```
脚本的使用方式如下，第一个参数即为要下载的镜像。
```s
decent@mac% ~/Downloads/image.sh docker.io/istio/examples-bookinfo-details-v1:1.16.2
```

在 mac 中 安装 ansible 方式参考[官方文档](https://docs.ansible.com/ansible/latest/installation_guide/intro_installation.html):
```s
python3 -m pip install --user ansible
```
如果上面安装的时候下载很慢，可以通过设置`https_proxy`的方式加速，当然你得先有proxy。

### 自动化工具集
总结常用的 shell 分析命令