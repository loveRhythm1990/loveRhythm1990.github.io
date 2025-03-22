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