---
layout:     post
title:      "CICD 系列2：自动打包 helm chart 并发布"
date:       2024-05-01 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - cicd
---

这个主要参考《[Chart Releaser Action to Automate GitHub Page Charts](https://helm.sh/docs/howto/chart_releaser_action/)》，在云原生领域，helm chart 也是应用事实上的打包标准，一般来说，我们构建 helm chart 之后，可以通过 helm chart tgz 包进行传输和发布，（比如在某些私有云环境中，通过 u 盘传送 chart，it works!）。

但是如有一个公共的 helm chart 仓库，我们就可以直接通过 helm install 来安装 chart 了，helm 有官方 github action 来做这个事情。如下，基本上也是可以粘贴使用。下面 github action 的触发时机是 pr 合并到 main 分支，以及发布一个 release。

使用下面 github action，有下面几点需要注意：1）helm chart 需要放在 charts 目录，由参数 `charts_dir` 指定；2）需要有另一个分支 `gh-pages` 来存放 chart 的版本信息，也就是 `index.yaml` 这个文件（chart 仓库用来维护 chart 版本信息的核心文件），这个分支是由 github action 维护的，但是需要我们提前创建。另外需要额外注意，这个分支最好不要包含任何敏感信息，一般来说，只包含 `index.yaml` 文件以及 Readme 文件，因为这个分支有可能被发布成 github pages(github pages 就是静态 http 服务器，本 loveRhythem1990 博客就是通过 github pages 发布，github 免费维护供我们白嫖)，一旦被发布成 github pages，这个分支的内容就是被公开的（当然可以只将特定目录发布成 github pages）

```yaml
name: Release Charts

on:
  push:
    branches:
      - main
  release:
    types:
      - published

jobs:
  release:
    permissions:
      contents: write
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Install Helm
        uses: azure/setup-helm@v4.0.0

      - name: Configure Git
        run: |
          git config user.name "$GITHUB_ACTOR"
          git config user.email "$GITHUB_ACTOR@users.noreply.github.com"

      - name: Run chart-releaser
        uses: helm/chart-releaser-action@v1.6.0
        with:
          charts_dir: charts
        env:
          CR_TOKEN: "${{ secrets.GITHUB_TOKEN }}"
          CR_RELEASE_NAME_TEMPLATE: "chart-{{ .Version }}"
          CR_SKIP_EXISTING: true
```

上面可以 helm chart 发布到 github repo 的 release 分支。有两种方式可以消费这个 chart: 1）在 github 界面下载安装 helm（或者通过git sdk 下载安装）；2）直接通过 helm install 来安装 helm chart，对于这种方式，需要将 `gh-pages` 发布成 github pages，如果是私有镜像仓库，则需要处理鉴权问题，直接通过 helm install 时，仓库的地址为：`https://<org-name>.github.io/<repo-name>`，需要注意带着 `.github.io`。添加 helm repo 时，可以先访问 url `https://<org-name>.github.io/<repo-name>/index.yaml` 看看能不能访问通，如果不能，则说明 github pages 发布有问题。



> 另外需要注意，对于私有 github repo 来讲，只有被发布成 github pages 的分支才是被公开的，其他分支，以及各种 release 都是私有的，需要提供用户名密码或者 token 才能被访问。

`helm/chart-releaser-action` action 在我们修改 `Chart.yaml` 的 version 字段时，会发布一个新 Chart。默认发布的 chart asset，其命名格式为 `chart-<version>`，比如 chart-0.0.3，因此想实现 chart 的版本控制也是非常方便的。