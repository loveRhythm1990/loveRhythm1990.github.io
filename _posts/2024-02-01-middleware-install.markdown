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


经常部署一些中间件用于测试，记录一些常用中间件的部署方式

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
