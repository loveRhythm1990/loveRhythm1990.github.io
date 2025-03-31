---
layout:     post
title:      "K8s 环境下中间件部署合集"
date:       2024-02-11 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - 运维
---

**目录**
- [minio](#minio)
- [mysql](#mysql)
- [rocketmq](#rocketmq)
  - [安装 nameserver](#安装-nameserver)
  - [安装 dashbroad](#安装-dashbroad)


经常部署一些中间件用于**测试**，记录一些常用中间件的部署方式。

### minio
```s
helm repo add minio https://charts.min.io/
helm install --create-namespace --namespace mostorage --set resources.requests.memory=512Mi \
	--set replicas=1 --set persistence.size=10G --set mode=standalone \
	--set rootUser=rootuser,rootPassword=rootpass123 \
	--set "buckets[0].name"=minio-mo \
	--set consoleService.type=NodePort minio minio/minio
```

### mysql
```s
helm install my-release oci://registry-1.docker.io/bitnamicharts/mysql
```

### rocketmq

#### 安装 nameserver
```s
helm pull oci://registry-1.docker.io/apache/rocketmq --version 0.0.1
# tar -zxvf rocketmq-0.0.1.tgz
kubectl create ns rocketmq
helm install rocket -n rocketmq ./rocketmq-0.0.1.tgz -f values.yaml
```
需要重写 values 中的资源配置，否则测试环境下资源不够起不来，将下面配置作为额外的 values.yaml 配置。
```yml
nameserver:
  resources:
    limits:
      cpu: 1
      memory: 2Gi
    requests:
      cpu: 100m
      memory: 100Mi

proxy:
  resources:
    limits:
      cpu: 1
      memory: 8Gi
    requests:
      cpu: 1
      memory: 200Mi

broker:
  resources:
    limits:
      cpu: 2
      memory: 4Gi
    requests:
      cpu: 1
      memory: 200Mi

controller:
  replicas: 1
  resources:
    limits: 
      cpu: 2
      memory: 4Gi
    requests:
      cpu: 1
      memory: 200Mi
```

#### 安装 dashbroad
使用 [https://github.com/apache/rocketmq-dashboard](https://github.com/apache/rocketmq-dashboard) 作为 dashbroad。安装完成之后需要将 8080 端口 port-forward 出来，然后使用 127.0.0.1:8080 地址就可以访问了。
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: rocketmq-dashbroad
  namespace: rocketmq
spec:
  replicas: 1
  selector:
    matchLabels:
      app: rocketmq-dashbroad
  template:
    metadata:
      labels:
        app: rocketmq-dashbroad
    spec:
      containers:
      - name: rq
        image: apacherocketmq/rocketmq-dashboard:latest
        env:
        - name: JAVA_OPTS
          value: "-Drocketmq.namesrv.addr=rocket-nameserver.rocketmq.svc:9876"
        ports:
        - containerPort: 8080
```

这里顺便介绍一下通过 binary 在本地部署 rocketmq 的方式，参考 rocketmq 的官方文档[本地部署 RocketMQ](https://rocketmq.apache.org/zh/docs/quickStart/01quickstart).

1. 下载二进制文件 `https://dist.apache.org/repos/dist/release/rocketmq/5.3.1/rocketmq-all-5.3.1-bin-release.zip`，下载后解压。
2. 启动 nameserver。启动成功之后，nameserver 监听在 127.0.0.1:9876 地址。
   ```s
   cd rocketmq-all-5.3.1-bin-release
   bin/mqnamesrv
   ```
3. 通过 local 模式部署 broker 和 proxy.
   ```s
   bin/mqbroker -n localhost:9876 --enable-proxy
   ```
4. 使用工具创建 topic，替换 test-topic 为希望的 topic 名字。
   ```s
   export NAMESRV_ADDR=localhost:9876
  
   # 创建一个 topic
   bin/mqadmin updateTopic -c DefaultCluster -t test-topic -r 8 -w 8

   # 查看 topic 列表
   bin/mqadmin topicList

   # 查看 topic 详情
   bin/mqadmin topicStatus -n <NameServer> -t <Topic名称>

   # 查看状态
   bin/mqadmin consumerStatus -g <ConsumerGroup名称> 

   # 查看消息积压队列
   bin/mqadmin consumerProgress -g <ConsumerGroup名称> 

   # 删除 topic
   bin/mqadmin deleteTopic -n 127.0.0.1:9876 -c DefaultCluster -t "%DLQ%YourGroup"
   ```


> proxy 需要监听 8080 端口，mac 电脑查看 8080 端口被谁占用的命令为： lsof -i :8080