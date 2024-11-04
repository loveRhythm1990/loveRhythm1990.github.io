---
layout:     post
title:      "docker harbor 管理 helm chart 生命周期"
date:       2024-08-01 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - cicd
---

**目录**

- [harbor 概述](#harbor-概述)
- [helm 命令行使用](#helm-命令行使用)
  - [传统 helm chart 仓库格式](#传统-helm-chart-仓库格式)
  - [oci 格式](#oci-格式)
- [打包上传 chart](#打包上传-chart)
- [在 pulumi 中安装 chart](#在-pulumi-中安装-chart)
  - [传统 helm chart 格式](#传统-helm-chart-格式)
  - [oci 格式的镜像仓库](#oci-格式的镜像仓库)
- [使用 helm sdk 安装 chart](#使用-helm-sdk-安装-chart)

### harbor 概述
[harbor](https://github.com/goharbor/harbor) 一般用来存储镜像，除此之外还可以存储 helm chart，并且有两种方式存储 helm chart，一种是 oci 格式。另一种是普通的 helm chart 仓库。使用 oci 格式时，跟 docker image 共用 tag，可能会发生冲突，因此要存储 oci 格式的 helm chart，通常需要一个专门的命名空间。以普通的 helm chart 存储时命令行和 sdk 兼容性更好一点，所以推荐使用普通 helm chart 存储。

### helm 命令行使用
#### 传统 helm chart 仓库格式
通过传统 helm chart 方式管理 helm chart，这个是推荐使用的方式。
```sh
# a. 登录
helm registry login -u<userName>  my.harbor-repo.com

# b. 添加 repo
# 其中 https://my.harbor-repo.com/chartrepo/ 这部分是固定的
# mocloud 是我们镜像仓库的地址。即使 helm registry 登录成功，添加 repo 仍然需要密码
helm repo add  --username <userName> --password <password>  \
  myrepo  https://my.harbor-repo.com/chartrepo/mocloud

# c. 上传一个 chart，分两步
# 第一步：安装 helm-push 插件
helm plugin install https://github.com/chartmuseum/helm-push
# 第二步：上传
helm cm-push ./nginx-0.1.0.tgz myrepo

# d. 安装 helm chart
helm install nginx --version 0.1.0 myrepo/nginx

# 其他 helm 命令
# 下载一个 helm chart 到本地，默认有个最新版本
helm pull repoName/chartName

# 创建一个新的 chart，包含默认的一些配置，（默认是 nginx 模板，需要我们自己修改）
helm create aChartName
```


#### oci 格式
以 oci 格式管理 helm chart。
```shell
# a. 登录，运行下面命令后需要输入密码。
helm registry login -u<userName>  my.harbor-repo.com

# b. 安装一个 helm chart 到 K8s。
helm install nginx oci://my.harbor-repo.com/mocloud-charts/nginx --version 0.1.0

# c. 推送一个 helm chart
helm push nginx-0.1.0.tgz oci://my.harbor-repo.com/mocloud-charts

# d. 下载一个 helm chart 到本地。
helm pull oci://my.harbor-repo.com/mocloud-charts/nginx --version=0.1.0
```

### 打包上传 chart
假设 chart 在项目中的路径为 ./charts，如 ./charts/guestbook，可以通过下面 github action 来打包上传 chart。 需要在项目中配置 secrets.OCI_REGISTRY_PASSWORD 以及 secrets.OCI_REGISTRY_USERNAME。

NOTE: 下面例子是以 oci 格式的 helm chart 管理的，如果需要以传统 helm chart 格式管理，请适当调整 helm 命令的使用。
```yaml
name: Release Helm Chart

on:
  push:
    branches:
      - main
    paths:
      - 'charts/**'

jobs:
  release:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout code
        uses: actions/checkout@v3

      - name: Set up Helm
        uses: azure/setup-helm@v3
        with:
          version: v3.12.0

      - name: Install yq
        run: |
          sudo add-apt-repository ppa:rmescandon/yq
          sudo apt-get update
          sudo apt-get install -y yq

      - name: Check if Chart version has changed
        id: version_check
        run: |
          CHART_VERSION=$(yq eval '.version' ./charts/guestbook/Chart.yaml)
          echo "Detected Chart version: $CHART_VERSION"
          
          echo ${{ secrets.OCI_REGISTRY_PASSWORD }} | helm registry login -u ${{ secrets.OCI_REGISTRY_USERNAME }} --password-stdin my.harbor-repo.com

          # Check if this version exists in the remote OCI registry
          REGISTRY="my.harbor-repo.com/mocloud-charts/guestbook"
          VERSION_EXISTS=$(helm pull oci://$REGISTRY --version $CHART_VERSION --destination /tmp || echo "not found")
          if [[ "$VERSION_EXISTS" == "not found" ]]; then
            echo "New Chart version detected"
            echo "version_changed=true" >> $GITHUB_ENV
          else
            echo "Chart version already exists, skipping deployment"
            echo "version_changed=false" >> $GITHUB_ENV
          fi

      - name: Package Helm Chart
        if: env.version_changed == 'true'
        run: |
          helm package ./charts/*
          echo "Chart packaged successfully."

      - name: Push to OCI Registry
        if: env.version_changed == 'true'
        run: |
          helm push *.tgz oci://my.harbor-repo.com/mocloud-charts
```

### 在 pulumi 中安装 chart
#### 传统 helm chart 格式
在安装时，可以指定镜像仓库的用户名密码。
```go
    _, err = helmv3.NewChart(ctx, "my-chart", helmv3.ChartArgs{
        Chart: pulumi.String("nginx"),
        FetchArgs: helmv3.FetchArgs{
            Repo:     pulumi.String("https://my.harbor-repo.com/chartrepo/mocloud"),
            Username: pulumi.String("username"),
            Password: pulumi.String("password"),
        },
        Version: pulumi.String("0.1.0"), 
    })
    if err != nil {
        return err
    }
    return nil
```

#### oci 格式的镜像仓库
在 pulumi 中安装 chart 时，需要先通过 helm 命令行登录仓库。对于 oci 格式的 helm 仓库，pulumi 现在不支持在代码中指定用户名密码，参考 [Helm OCI registry support is missing authentication](https://github.com/pulumi/pulumi-kubernetes/issues/1914)
```go
    _, err = helmv3.NewRelease(ctx, "nginx-ingress-release", &helmv3.ReleaseArgs{
        Chart:     pulumi.String("oci://my.harbor-repo.com/mocloud-charts/nginx"),
        Version:   pulumi.String("0.1.0"),
        Namespace: pulumi.String("default"),
    })
    if err != nil {
        return err
    }
```

### 使用 helm sdk 安装 chart
使用 helm sdk 安装 chart 是可行，但是限制较多，主要原因是 helm 相关接口大多是为 helm 命令行（binary）准备的。
helm 命令行在调用 sdk 接口之前还有很多参数处理逻辑。如果我们是使用 sdk 接口需要自己去处理参数，比如无法使用 upgrade-install 命令，需要自己判断 release 是否安装。
推荐使用 helm 命令行，包括在代码中。sdk 使用示例如下（非最佳代码，只是跑通了）。
```go
package main

import (
	"errors"
	"fmt"
	"helm.sh/helm/v3/pkg/action"
	"helm.sh/helm/v3/pkg/chart/loader"
	"helm.sh/helm/v3/pkg/cli"
	"helm.sh/helm/v3/pkg/registry"
	"helm.sh/helm/v3/pkg/release"
	"helm.sh/helm/v3/pkg/storage/driver"
	"os"
	"time"
)

const (
	kubeconfig = ``
	myNamespace = "test"
)

func main() {
	// 配置 Helm
	settings := cli.New()

	// 创建 action configuration
	actionConfig := new(action.Configuration)
	err := actionConfig.Init(
		settings.RESTClientGetter(),
		myNamespace,
		os.Getenv("HELM_DRIVER"),
		debug)
	must(err)

	// 注册仓库
	registryClient, err := registry.NewClient()
	must(err)

	err = registryClient.Login(
		"my.harbor-repo.com",
		registry.LoginOptBasicAuth("username", "password"),
	)
	must(err)
	actionConfig.RegistryClient = registryClient

	// 替换为你的仓库地址和 chart 名称
	chartRef := "oci://my.harbor-repo.com/mocloud-charts/nginx"

	// 加载 chart
	puller := action.NewPullWithOpts(action.WithConfig(actionConfig))
	puller.SetRegistryClient(registryClient)
	puller.Version = "0.1.0"
	puller.Settings = settings
	puller.DestDir = "/Users/lr90/helm"
	_, err = puller.Run(chartRef)
	must(err)

	chart, err := loader.Load("/Users/lr90/helm/nginx-0.1.0.tgz")
	must(err)

	if releaseExist(actionConfig, "nginx") {
		fmt.Println("nginx already installed.")
		upgrade := action.NewUpgrade(actionConfig)
		// upgrade.Install = true // does not work
		upgrade.Timeout = time.Minute * 10
		_, err = upgrade.Run("nginx", chart, map[string]interface{}{})
		must(err)
	} else {
		fmt.Println("nginx not installed.")

		install := action.NewInstall(actionConfig)
		install.ReleaseName = "nginx"
		install.Timeout = time.Minute * 10
		_, err = install.Run(chart, map[string]interface{}{})
		must(err)
	}
	fmt.Println("Chart installed successfully")
}

func must(err error) {
	if err != nil { panic(err) }
}

func debug(format string, v ...interface{}) {
	fmt.Sprintf(format, v...)
}

func releaseExist(cfg *action.Configuration, name string) bool {
	histClient := action.NewHistory(cfg)
	histClient.Max = 1
	versions, err := histClient.Run(name)
	if errors.Is(err, driver.ErrReleaseNotFound) || isReleaseUninstalled(versions) {
		return false
	}
	return true
}

func isReleaseUninstalled(versions []*release.Release) bool {
	return len(versions) > 0 && versions[len(versions)-1].Info.Status == release.StatusUninstalled
}
```
