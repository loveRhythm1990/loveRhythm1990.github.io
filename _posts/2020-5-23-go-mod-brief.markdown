---
layout:     post
title:      "Golang mod 速记"
date:       2020-05-23 19:23:00
author:     "weak old dog"
header-img-credit: false
tags:
    - Golang
---

Go 1.11以及1.12版本开始支持Go mod，使用Go mod来管理依赖，本文理解思路以及记录一些常用命令。

Go 1.11/1.12版本启用go mod的条件为：1）代码路径在`GOPATH`之外，并且 2）包的根目录含有一个`go.mod`文件，当代码在`GOPATH`下面时，即使含有`go.mod`文件也不启用。从go 1.13开始，无论代码在不在`GOPATH`路径下，只要有`go.mod`文件，都是启用Go mod的。[官方文档](https://blog.golang.org/using-go-modules)给出的例子如下，是直接在`GOPATH`外面直接创建目录，编写代码：
```go
package hello

import "rsc.io/quote"

func Hello() string {
    return quote.Hello()
}
```
还写了一个测试文件，使用`go test`命令运行测试文件时，会拉取依赖。在写好go文件以及测试文件后，运行`go mod init example.com/hello`初始化一个go mod mudule，其中`example.com/hello`是代码引入时的路径名。

#### 列出所有依赖
命令`go list -m all`列出当前的module，以及其所有依赖，在golang代码中，如果在import中引入了第三方包，在执行`go test`或者`go build`时，会自动拉取第三方包，并放到`$GOPATH/pkg/mod`目录下面，已有所有的项目都会共享这些依赖。如果所依赖的第三方包也有其他依赖，也会被拉下来，但是不会显示在`go.mod`文件中。go命令拉取第三方包时，默认是拉取最新的（`Latest`）代码，（`Latest`的定义为：最新的打了tag的稳定版本，非[prerelease](https://semver.org/#spec-item-9)的；否则为最新的打了标签的prerelease版本；否则为最新的没有打tag的版本）。在`go list -m all`的输出中，`golang.org/x/text v0.0.0-20170915032832-14c0d48ead0c`表示的就是最新的没有打tag的版本，表示的是一个commit号。

#### 升降级
当我们需要特定版本的依赖时，我们可以使用`go get`命令来获取依赖的特定版本，比如：
```s
go get rsc.io/sampler@v1.3.1
```
第三方包版本遵循的格式为：v(major version).(minor version).(patch version)。使用`go list -m -versions rsc.io/sampler`命令可以查看第三方包`rsc.io/sampler`的所有版本，使用`go get`升降级之后，可以再使用`go list`命令查看项目的依赖（预期会变成我们使用go get所拉的版本）。在官方文档给出的例子中，`go list`给出的例子为：
```s
$ go list -m all
example.com/hello
golang.org/x/text v0.0.0-20170915032832-14c0d48ead0c
rsc.io/quote v1.5.2
rsc.io/sampler v1.3.0
```
其中，`quote`是我们直接引用的，应该是最新的的，但是另外两个不是最新版本的，是`quote`所引用的第三方包，因此，我们可以使用`go get`来获取最新版本。

#### 清空不需要的依赖
使用`go mod tidy`清空没有用的依赖。

#### 复制依赖到vendor目录
`go mod vendor`，这个命令在项目的根目录建一个vendor目录，并将依赖从mod cache复制到vendor目录。

#### 下载依赖
go mod download，下载module到本地缓存，这个命令在go1.13挺好用的，在go1.12貌似有些问题。
其他go mod子命令可以使用go help mod查看。

#### 设置代理
go1.13默认的代理不能访问，要设置中国区代理：

`go env -w GOPROXY=https://goproxy.cn,direct `

后面的direct表示如果使用这个代理无法访问，则直接访问。另外使用秘钥访问私有仓库的设置为：

`git config --global url."ssh://git@gitlab.mycompany.com:".insteadOf "https://gitlab.mycompany.com/"`

设置完成之后，可以通过文件`~/.gitconfig`查看。

go 1.12可以这么设置代理，（话说goproxy.io是哪个代理？还挺好用的，其网址为：[https://goproxy.io/](https://goproxy.io/)）
```s
export GOPROXY=https://goproxy.io
```

#### GO111MODULE
控制或者启用GO mod的环境变量，使用如下：
* If GO111MODULE=off, the go command ignores go.mod files and runs in GOPATH mode.
* If GO111MODULE=on, the go command runs in module-aware mode, even when no go.mod file is present. Not all commands work without a go.mod file: see Module commands outside a module.
* If GO111MODULE=auto or is unset, the go command runs in module-aware mode if a go.mod file is present in the current directory or any parent directory (the default behavior).

#### 参考
[golang blog - Using Go Modules](https://blog.golang.org/using-go-modules)

[Go Modules Reference](https://golang.org/ref/mod)

[goproxy配置](https://goproxy.cn/)