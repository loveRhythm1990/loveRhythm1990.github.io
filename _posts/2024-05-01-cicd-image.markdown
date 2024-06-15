---
layout:     post
title:      "CICD —— Github action 自动 build/push 镜像"
date:       2024-05-01 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - cicd
---

介绍如何通过 github action 以及 pulumi 实现 cicd，总体来说，github action 以及 pulumi 的使用很难评，它们确实能帮忙完成一些自动化的流程，比如自动升级、部署，定时任务，手动触发任务等。但有时候使用起来非常 trick，调试起来难度也不小，使用 github action 以及 pulumi 就是不断踩坑的过程。（注：使用起来是不是 trick 主要取决于 github action 的实现以及使用）。

大概会有几篇文章来介绍通过 github action 以及 pulumi 实现 cicd，由于时间关系，一开始会直接粘贴一些实现，一些细节会在使用过程中慢慢补充。另外这些文章也可以称为 “如何白嫖一套 cicd 系统”（但是 github runner 是有时间配额的，超过了就不能跑了。。）。

下面是一个自动打镜像并推送到阿里云镜像仓库的例子（推送到 dockerhub 是一样的），基本上可以粘贴复制使用。主要使用的是 `docker/build-push-action` 这个 github action，具体来说有下面几步：
1. 使用 `actions/checkout` 把代码拉下来。
2. 登录到 acr，需要实现把镜像仓库的用户名、密码放到 github secret 中。
3. 配置镜像 tag，默认配置两个 tag: main-{时间戳}，sha-{commit}。 
4. 构建并 push 镜像，使用根目录下的 `Dockerfile` 打镜像，另外因为在 build 的时候需要访问其他私有镜像仓库（多个私有镜像直接有引用），这里需要配置 github git token，这里也是配置到 github action secret 中了。另外在配置 git token 时，有的 github action 只需要一个 token，有的需要一个完整的 token 串（包含用户名），所以在遇到鉴权问题时，不妨切换一下试试。 
   
这个 job 的触发时机是 pr 合并到 main 分支，所有的 github workflow 需要放到项目根目录的 `.github/workflows` 目录。

```yaml
name: release-acr

on:
  push:
    branches:
      - main

jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: login to acr
        uses: aliyun/acr-login@v1
        with:
          login-server: registry.cn-hangzhou.aliyuncs.com
          username: "${{ secrets.ACR_USERNAME }}"
          password: "${{ secrets.ACR_PASSWORD }}"
          region-id: cn-hangzhou

      - name: set up docker buildx
        id: buildx
        uses: docker/setup-buildx-action@v1

      - name: docker meta
        id: meta
        uses: docker/metadata-action@v4
        with:
          images: registry.cn-hangzhou.aliyuncs.com/app-ns/app-repo
          tags: |
            type=sha
            type=ref,event=branch,suffix=-{{date 'YYYYMMDD'}}
            type=semver,pattern={{version}}

      - name: build and push
        uses: docker/build-push-action@v2
        with:
          builder: ${{ steps.buildx.outputs.name }}
          context: .
          secrets: "github=${{ secrets.MY_GIT_TOKEN }}"
          file: Dockerfile
          platforms: linux/amd64
          push: true
          tags: ${{ steps.meta.outputs.tags }}
```