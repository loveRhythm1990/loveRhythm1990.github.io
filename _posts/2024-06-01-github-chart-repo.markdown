---
layout:     post
title:      "通过 github action 自动打包项目 helm chart 并发布"
date:       2024-06-01 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - cicd
---

**目录**
- [helm chart 概述](#helm-chart-概述)
- [自动打包 chart 到 github repo](#自动打包-chart-到-github-repo)
- [发布 chart 到 K8s 集群](#发布-chart-到-k8s-集群)

### helm chart 概述
在云原生领域，helm chart 是事实上的应用交付标准，一般来说，我们构建 helm chart 之后，有两种方式可以发布 chart: 1）可以通过 helm chart tgz 压缩包进行传输和发布；2）将 chart 放到仓库（公共或者私有），大家通过 helm repo add 命令添加 repo 之后，可以直接使用 helm install 命令进行安装，比如下面是安装 openkruise 的命令。
```s
# Firstly add openkruise charts repository if you haven't do this.
$ helm repo add openkruise https://openkruise.github.io/charts/
# Install the latest version.
$ helm install kruise openkruise/kruise --version 1.6.3
```
本文讨论下如何将 github 仓库变成一个 helm chart 仓库，并使用 github action 自动打包 chart。

### 自动打包 chart 到 github repo
这个主要参考 [Chart Releaser Action to Automate GitHub Page Charts](https://helm.sh/docs/howto/chart_releaser_action/)。使用下面 github action，有下面几点需要注意：
1. helm chart 需要放在 charts 目录，或者由 chart-releaser-action 的参数 charts_dir 指定；
2. 需要有另一个分支 `gh-pages` 来存放 chart 的索引信息，也就是 `index.yaml` 这个文件（chart 仓库用来维护 chart 版本信息的核心文件），这个分支是由 github action 维护的，但是需要我们提前创建，我们创建这个分支时，只需要包含 readme 文件就可以了，这个分支的其他内容由 github 维护。我们在新建一个新的 github project 的时候，第一个 commit 建议总是 readme 文件。

另外 gh-pages 这个分支最好不要包含任何敏感信息，一般来说，只包含 `index.yaml` 文件以及 Readme 文件，因为这个分支有可能被发布成 github pages，一旦被发布成 github pages，这个分支的内容就是被公开的（也可以只将特定目录发布成 github pages）。github pages 就是静态 http 服务器，我个人的博客就是通过 github pages 发布。

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
          CR_TOKEN: "$\{\{ secrets.GITHUB_TOKEN \}\}"
          CR_SKIP_EXISTING: true
```

### 发布 chart 到 K8s 集群

将 helm charts 发布为 repo 的 release 资源后。有两种方式可以消费这个 chart。

1）在 github 界面下载安装 chart（或者通过 git sdk 下载 chart tgz 并安装），git sdk 可以使用 [https://github.com/google/go-github/](https://github.com/google/go-github/)，初始化的方式为使用 github token，可以参考下面例子。

```go
func NewGitClient(cfgGithubToken string) *github.Client {
	if cfgGithubToken == "" {
    panic("nil token")
  }
	client := github.NewClient(nil).WithAuthToken(token)
	return client
}

func DownloadChart(c *github.Client, org, repo, tag, assetName string) (string, error) {
	release, resp, err := c.Repositories.GetReleaseByTag(context.TODO(), org, repo, tag)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	var assetID *int64
	for _, asset := range release.Assets {
		if *asset.Name == assetName {
			assetID = asset.ID
			break
		}
	}
	if assetID == nil {
		return "", fmt.Errorf("couldn't find asset %s", assetName)
	}

	readerCloser, _, err := c.Repositories.DownloadReleaseAsset(context.TODO(), org, repo, *assetID, http.DefaultClient)
	if err != nil || readerCloser == nil {
		return "", fmt.Errorf("unexpected error or nil reader downloading asset %v, %v", err, readerCloser)
	}
	defer readerCloser.Close()

	tempFile, err := os.CreateTemp("", "chart-*.tgz")
	if err != nil {
		return "", err
	}
	defer tempFile.Close()

	if _, err = io.Copy(tempFile, readerCloser); err != nil {
		return "", err
	}
	return tempFile.Name(), nil
}
```

2）直接通过 helm install 来安装 helm chart，对于这种方式，需要将 gh-pages 分支发布成 github pages，如果是私有镜像仓库，则需要处理鉴权问题。直接通过 helm install 时，仓库的地址为：`https://<org-name>.github.io/<repo-name>`，需要注意带着 `.github.io`。添加 helm repo 时，可以先访问 url `https://<org-name>.github.io/<repo-name>/index.yaml` 看看能不能访问通，如果不能，则说明 github pages 发布有问题。

将 github repo 的特定分支发布成 github pages 的方式为：点击仓库主页右上角的 Settings，在设置页面中，向下滚动到 "Pages" 部分。在 "Source" 下拉菜单中，选择你要用于 GitHub Pages 的分支（例如 main 或 gh-pages）。另外需要注意，对于私有 github repo 来讲，只有被发布成 github pages 的分支才是被公开的，其他分支，以及各种 release 都是私有的，需要提供用户名密码或者 token 才能被访问。

helm/chart-releaser-action 在我们修改 Chart.yaml 的 version 字段时，会发布一个新 Chart。默认发布的 chart asset，其命名格式为 `<chartName>-<version>`，比如 myProject-0.0.3，因此想实现 chart 的版本控制也是非常方便的。