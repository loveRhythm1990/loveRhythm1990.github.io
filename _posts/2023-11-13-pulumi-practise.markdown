---
layout:     post
title:      "使用 pulumi 进行 K8s 配置管理与交付"
date:       2023-11-18 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - cicd
---

**目录**
- [pulumi 入门](#pulumi-入门)
  - [环境初始化](#环境初始化)
  - [写交付代码](#写交付代码)
  - [pulumi 是怎么工作的](#pulumi-是怎么工作的)
- [pulumi 中的依赖](#pulumi-中的依赖)
- [pulumi 常用命令](#pulumi-常用命令)
- [使用技巧](#使用技巧)
  - [将 pulumi.StringOutput 转换为 pulumi.ID](#将-pulumistringoutput-转换为-pulumiid)
  - [结构化配置](#结构化配置)
- [配置 github action 运行 preview/up](#配置-github-action-运行-previewup)
- [pulumi 运维注意事项](#pulumi-运维注意事项)

### pulumi 入门
[pulumi](https://www.pulumi.com/) 是一个配置管理工具，是 terraform 的替代品，可以通过声明式的配置对 K8s 集群（及集群中的组件）进行管理交付，其所倡导的思想是 `代码即配置`。用户可以根据自己的语音偏好来写交付代码（如 go/javascript/typescript 等）。
本文入门部分包括环境配置，以及编写简单的 go 交付代码。

本文使用 pulumi 中的 kubernetes-go 模板初始化项目，并用 [kind](https://kind.sigs.k8s.io/) 启动一个 K8s 集群作为 pulumi 的测试集群。默认情况下 project 寻找 kubeconfig 的方式跟 kubectl 是一致的：1）先找 $KUBECONFIG 环境变量；2）再找 ~/.kube/config 文件。在 cicd 实践中，我们可以将 kubeconfig 放在 github action secret 中。

在 [kind](https://kind.sigs.k8s.io/) 环境中，将 kubeconfig 写到 ~/.kube/config 文件的命令为：kind export kubeconfig -n kind。其中 -n kind 是指定 kind 集群的名字。`kubernetes-go` 模板，顾名思义，生成的 project 是 go 语言的，并且是直接跟一个 kubernetes 集群交互。

> cicd 实现因供应商而异。对阿里云来说，我们不仅要实现组件交付，还要实现 IaaS 管理，比如 vpc/switch/K8s集群等。pulumi 的 template 也没有明显的界限，一般来讲，我们生成 go 语言的 project，可以引入 aws 或者 aliyun 的 sdk 跟云供应商交互，比如创建一个 vpc 等，不过从最佳实践来讲，我们要使用对应环境的目标，比如 aws、azure等。通过 `pulumi new -l` 可以查看 pulumi 支持的所有模板。

#### 环境初始化
使用下面步骤初始化一个项目，初始化时尤其需要注意当前使用的 pulumi 账号，使用 `pulumi whoami` 命令可以查看当前登录的账号，表明对资源进行了错误的运维。本文以 kubernetes-go 模板构建项目。
```s
# 1.（可选）退出之前的登录，如果经常使用多个账号，比如公司账号和个人账号，建议首先执行这一步，避免对 project 资源进行了错误的配置。
pulumi logout
# 2. 配置 pulumi 登录的 token。
export PULUMI_ACCESS_TOKEN=pul-3d67f1780xxxxxxxx

# 3. 登录，登录之后需要确认下对应的组织以及账号信息是否匹配。
lr90@sj % pulumi login
Logging in using access token from PULUMI_ACCESS_TOKEN
Logged in to pulumi.com as loveRhythm1990 (https://app.pulumi.com/loveRhythm1990)

# 4. 创建一个 project，首先新建一个目录，然后使用 kubernetes-go 模板初始化项目。   
mkdir kind-cluster && cd kind-cluster
pulumi new kubernetes-go

# 5. 确认新建的 stack，新建的 stack 没有任何资源，所以 RESOURCE COUNT 为 n/a
lr90@sj % pulumi stack ls
NAME  LAST UPDATE  RESOURCE COUNT  URL
dev*  n/a          n/a             https://app.pulumi.com/loveRhythm1990/kind-cluster/dev
```

#### 写交付代码
交付代码也就是我们的业务代码，也就是 cicd 中的 cd，pulumi 编程模式中大量使用了预定义的资源模型，有时候看起来会有点怪。而且在编写代码时要有意识在构建一棵树（即处理依赖关系，这是 pulumi 最主要的编程模型），或者是构建 DAG 图，一定要处理好资源之间的依赖。

下面是部署 cert-manager 到 K8s 集群的例子，同时创建了一个 issuer 资源，这里需要注意，cert-manager 一定要在 issuer 资源创建之前部署，因此用了 `DependsOnInputs` 约束，这个是chart 的特有约束，表明要等待 chart 的所有 subresource 资源都创建完成。pulumi 创建资源的顺序并不是代码的顺序，而是 DAG 图中的顺序。下面是项目的全部代码。
```go
package main
import (
   helmv3 "github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/helm/v3"
   "github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/yaml"
   "github.com/pulumi/pulumi/sdk/v3/go/pulumi"
)

func main() {
   pulumi.Run(func(ctx *pulumi.Context) error {

      certManager, err := helmv3.NewChart(ctx, "cert-manager-chart", helmv3.ChartArgs{
         Chart:     pulumi.String("cert-manager"),
         Version:   pulumi.String("v1.5.4"),
         Namespace: pulumi.String("cert-manager"),
         FetchArgs: helmv3.FetchArgs{
            Repo: pulumi.String("https://charts.jetstack.io"),
         },
         Values: pulumi.Map{
            "installCRDs": pulumi.Bool(true),
         },
      })
      if err != nil { return err }
		
      _, err = yaml.NewConfigFile(ctx, "cert-manager-issuer", &yaml.ConfigFileArgs{
         File: "./issuer.yaml",
      }, pulumi.DependsOnInputs(certManager.Ready))
      if err != nil { return err }
		
      return nil
   })
}
```

#### pulumi 是怎么工作的
pulumi 遵循 K8s 的设计模式：声明式配置。因此代码中声明的资源配置都是 `Spec`，也即集群中最终应该变成的样子。根据文档，pulumi 架构如下图，其中：
* Laguage host: 理解成我们自己写的交付代码，有两点需要关注：1）sdk 语言，比如用 go 还是 typescript；2）provider，是 Kubernetes 还是 aws 或者 alyun。
* CLI and Engine: pulumi 核心控制器，由 pulumi 团队维护。根据 state 的 diff 对集群中的资源进行 CRUD 操作。
* Last Deploy State: 资源的 state 列表，可通过命令 [pulumi state](https://www.pulumi.com/docs/cli/commands/pulumi_state/) 管理 state。 state 是保存在 pulumi cloud 中的，一般不需要我们处理。除非配置出问题了需要我们手动干预，比如手动强制删除资源。 

![java-javascript](/pics/pulumi-work.png){:height="60%" width="60%"}


### pulumi 中的依赖
pulumi 将尽可能并行创建/更新代码中声明的资源，但是一般来说多个资源之间可能有依赖关系，在遇到依赖时，会先创建被依赖的资源。pulumi 中资源的依赖会记录在 state 中。

依赖管理是 pulumi 的核心功能，这部分在 pulumi 的文档 [Inputs & outputs](https://www.pulumi.com/docs/concepts/inputs-outputs/) 中介绍，思路就是通过资源的 output 以及 input 来构建依赖。比如 B 资源依赖 A，C 资源依赖 A/B 资源等，这样就构成了一个依赖树，也就是 DAG 图。

在配置资源依赖时，应尽量简单，不要有太多的中间依赖，否则可能会构建失败：waiting for RPCs: rpc error: code = InvalidArgument desc = invalid dependency URN: missing required URN。一般来说，依赖有多种方式：
* A 资源的输出作为 B 资源的输入。比如 A 资源的 pulumi.StringOutput 作为 B 资源的 pulumi.StringInput。
* 在创建资源的 Options 中，显式的使用 pulumi.Dependson([]pulumi.Resource{A}) 声明依赖。
* 其他方式，比如上面例子中使用 pulumi.DependsOnInputs 声明对 Chart 所有子资源的依赖，等 Chart 所有子资源 ready 之后，再创建其他资源。

### pulumi 常用命令
记录一下常用的 pulumi 命令。
```s
# 1. 创建一个新的 pulumi stack
pulumi stack init staging
 
# 2. 配置云供应商的ak/sk，以阿里云为例，通常我们在自己测试时会这么用，其他情况下，会通过 github action 的 secret 来配置 token
pulumi config set alicloud:accessKey --secret kxxx
pulumi config set alicloud:secretKey --secret vxxx
   
# 3. 查看 stack 中的所有资源，并导出到 stack.json 文件。查看所有资源的 urn
pulumi stack export --file stack.json
pulumi stack --show-urns
   
# 4. 预览和执行，这两个其实是最常用的命令，80% 的时间我们都在使用这个命令。 
# 这两个命令，后面可以加 --diff，来显式详情
pulumi preview 
pulumi up

# 5. 从 pulumi cloud 中删除一个资源，
# 首先需要知道资源的 urn，然后才能删除。我们一般在 K8s 集群中资源被删除，但是 pulumi cloud 中的 state 没有被删除时
# 需要这么做，否则 pulumi 会一直报找不到资源的错误 
pulumi state delete urn:pulumi:dev::cos-component::kubernetes:core/v1:Namespace::someresource

# 6. 允许 pulumi 强制更新资源，当 kubernetes 资源有字段冲突的时候（server side apply conflict），需要这么做
PULUMI_K8S_ENABLE_PATCH_FORCE="true" pulumi up

# 7. 添加、获取配置，比如添加一个 key，其值为 value，其中 --secret 表示要将 value 加密，否则是不需要的。
# 当 value 被加密时，通过 pulumi config get 也能将明文拿出来
pulumi config set --secret key value
pulumi config get key

# 8. 复杂结构配置，有时候，我们会直接在 pulumi.yaml 中配置复杂结构体，或者是一个结构体列表，
# 在 pulumi 代码中，直接通过 pulumi.RequireObject 来将配置反序列为一个结构体。
# 比如可以在配置文件中，配置一个 UserInfo 的列表，然后通过下面命令配置列表第一个元素的 username 字段
type UserInfo struct {
   Username string `json:"username"`
} 
pulumi config set --secret --path 'authList[0].username' abc
```

### 使用技巧
#### 将 pulumi.StringOutput 转换为 pulumi.ID
pulumi 的各种输入输出之间经常需要转换，pulumi.StringOutput 与 pulumi.ID 本质都是 string 类型，但是不能直接转换，还是需要依赖 ApplyT 之间进行转换。
```go
stringOutput := pulumi.String("example-string").ToStringOutput()
// Convert StringOutput to ID
idOutput := stringOutput.ApplyT(func(v string) pulumi.ID {
   return pulumi.ID(v)
}).(pulumi.IDOutput)
```
在 ApplyT 方法内部，我们可以得到 pulumi.StringOutput 内部的基本类型 string，然后转换为 pulumi.ID 返回。注意 ApplyT 的参数 func 也比较灵活，一般下面两种签名：
* func (v U) T
* func (v U) (T, error)

其中 v 需要可被赋值给对应的 output(应用 applyT 的 output)，对于 T 我们可以返回一个基本类型，如 string/int，返回基本类型时，ApplyT 将返回对应的 output 类型，比如 pulumi.StringOutput、pulumi.IntOutput；我们也可以直接返回 output 类型（如 pulumi.StringOutput 等）；如在上面中，我们返回了 pulumi.ID 类型，因此 ApplyT 的返回类型为 pulumi.IDOutput。

注意在上面实现中，我们不能写成下面这个样子，因为 string 的 output 类型是 pulumi.StringOutput，其不能直接转换为 pulumi.IDOutput，会 panic: interface conversion: internal.Output is pulumi.StringOutput, not pulumi.IDOutput。
```go
idOutput := stringOutput.ApplyT(func(v string) string {
   return v
}).(pulumi.IDOutput)
```

#### 结构化配置
结构化配置是指在 Pulumi.dev.yaml 中配置结构体配置，并在代码中反序列化为结构体，这部分内容可以参考官方文档：[Structured Configuration](https://www.pulumi.com/docs/concepts/config/#structured-configuration)，不过有个问题文档中没有说清楚，配置中的字段跟 go 中的 struct 字段是如何映射的？不过我们稍微研究一下 `cfg.RequireObject` 代码就会发现，代码中用的是 json.Unmarshal，所以我们要为 struct 配置 json tag。
```go
type foo struct {
	Name string `json:"name"`
	ID   string `json:"id"`
}
```

### 配置 github action 运行 preview/up
《[github action](https://docs.github.com/en/actions)》 依据 github 提供的 runner 虚拟机（容器）来执行一些 ci 动作，比如测试、代码检查、pulumi 预览、执行等。在未付费的情况下，github runner 一般是有运行时间限制的。我们以执行 pulumi priview 为例子，看一下对应的 yaml 配置。下面的配置文件需要放在项目的 .github/workflows 目录。

下面配置中，ALICLOUD_AK 与 ALICLOUD_SK secret 是云供应商的 ak/sk。PULUMI_TOKEN 是登录 pulumi 的 token。 PULUMI_K8S_ENABLE_PATCH_FORCE 表示在发生 ssa 冲突时，允许强制更新集群资源。
```yaml
name: resources preview

on:
  pull_request:
    branches:
    - 'main'
    paths:
    - 'path/to/project/**'
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
    - name: checkout repository
      uses: actions/checkout@v3

    - name: set up go
      uses: actions/setup-go@v3
      with:
        go-version: 1.18.x
        cache: true

    - name: new-dev preview
      uses: pulumi/actions@v4.5.0
      if: github.base_ref == 'main'
      env:
        ALICLOUD_ACCESS_KEY: $\{\{ secrets.ALICLOUD_AK \}\}
        ALICLOUD_SECRET_KEY: $\{\{ secrets.ALICLOUD_SK \}\}
        PULUMI_ACCESS_TOKEN: $\{\{ secrets.PULUMI_TOKEN \}\}
        PULUMI_K8S_ENABLE_PATCH_FORCE: "true"
      with:
        pulumi-version: ^3
        command: preview
        diff: true
        stack-name: dev
        work-dir: path/to/project
```

### pulumi 运维注意事项
保持自身状态良好（运维人员的基本素养，状态不好时，不要做线上运维），一般来说，通过将 pulumi 运维配置为自动化任务可避免手动运维。目前总结了下面注意事项：
1. 操作前保持代码最新，总是使用 git fetch/git rebase 拉最新代码，devops 项目一般更新比较频繁。
2. 使用 pulumi stack ls 确认当前的 stack，如果当前 stack 跟预期不一致，使用 pulumi stack select {stack} 选择特定 stack。
3. 总是使用 pulumi preview 预览修改结果，使用 preview 查看输出时，再次确认要修改的 project 以及 stack。使用 pulumi preview --diff 可以查看变更的具体内容。
4. 不要把秘钥上传到 github，比如云供应商的 ak/sk，token 等。