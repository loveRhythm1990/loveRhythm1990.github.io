---
layout:     post
title:      "CICD 系列3：通过 pulumi 发布 chart 到 K8s 集群"
date:       2024-05-02 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - cicd
---

[pulumi](https://www.pulumi.com/) 是 terraform 的替代者，主张 `代码即配置`，通过 pulumi 可以自动化完成发布任务。pulumi 的使用以及设计思想有点复杂，有时间会专门补充，这里只介绍如何使用 pulumi 安装 chart 到 K8s 集群。

其实使用 pulumi 安装 chart 跟使普通 go 代码差不多，只不过 pulumi 中将 K8s 集群抽象为 Provider 对象，我们在安装 helm chart 时，只需要指定这个 Provider 就可以了，(其他任何资源也是如此，比如一个 yaml 文件描述的资源)。

一般来说，pulumi 安装 helm 的方式如下，下面是安装 cert-manager chart，通过 `Path` 指定 chart 的路径，Path 指定的是本地文件的路径。
```go
	values := pulumi.Map{
		"prometheus": pulumi.Map{
			"enabled": pulumi.Bool(false),
		},
		"image": pulumi.Map{
			"repository": pulumi.String(cfg.Require("cert-manager-repo")),
			"tag":        pulumi.String(version),
		},
	}
	chartResource, err = helmv3.NewChart(ctx, "cert-manager", helmv3.ChartArgs{
		Namespace: pulumi.String(namespace),
		Values:    values,
		Path:      pulumi.String(expectedTgzPath),
	}, pulumi.Provider(provider))
	if err != nil {
		return err
	}
  // 将 chartResource 作为其他组件的依赖。
```

除了指定本地 helm chart 路径，pulumi 提供了下面参数供我们安装一个远程 helm repo 中的 helm chart。其中：
* Repo: 仓库地址，即：`https://<org-name>.github.io/<repo-name>`
* Chart: 我们的 chart 名字，在 chart.yaml 中指定的名字。
* Version: chart 的版本。
* FetchArgs: 用户名密码等信息，如果拉私有仓库的 helm chart release 是需要填写用户名密码的。
  
```go
	// (Remote chart) The repository name of the chart to deploy. Example: "stable".
	Repo pulumi.StringInput
	// (Remote chart) The name of the chart to deploy.  If Repo is specified, this chart name will be prefixed
	// by the repo name.
	// Example: Repo: "stable", Chart: "nginx-ingress" -> "stable/nginx-ingress"
	// Example: Chart: "stable/nginx-ingress" -> "stable/nginx-ingress"
	Chart pulumi.StringInput
	Version pulumi.StringInput
	FetchArgs FetchArgsInput
```

另外，在 pulumi 部署使用过程中，还有另一个工具非常有用，就是 github sdk `github.com/google/go-github/v58/github`，该 package能够拉取 github 仓库中的内容，然后我们再借助 pulumi 部署到集群中。github 仓库中的内容包括：yaml 资源描述文件、release 文件等。尤其在私有 github 仓库中，这个 sdk 非常有用。这个 sdk 的使用方式如下。

其中 downloadChartByTag 通过一个 client 以及 tag 版本将一个 helm chart 下载到本地的一个临时文件。

```go
import "github.com/google/go-github/v58/github"

// 通过 token 初始化一个 client
func newGitClient(cfg *config.Config) *github.Client {
	token := cfg.Require(cfgGithubToken)
	client := github.NewClient(nil).WithAuthToken(token)
	return client
}

const (
  owner = "github repo 组织"
  repo = "github repo 名字"
)

func downloadChartByTag(c *github.Client, version string) (string, error) {
	tag := fmt.Sprintf("chart-%s", version)
	assetName := fmt.Sprintf("myrepo-%s.tgz", version)

	release, resp, err := c.Repositories.GetReleaseByTag(context.TODO(), owner, repo, tag)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	var assetID *int64
	for _, asset := range release.Assets {
		if asset.GetName() == assetName {
			assetID = asset.ID
			break
		}
	}
	if assetID == nil {
		return "", fmt.Errorf("asset %s not found", assetName)
	}

	readerCloser, _, err := c.Repositories.DownloadReleaseAsset(context.TODO(), owner, repo, *assetID, http.DefaultClient)
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

