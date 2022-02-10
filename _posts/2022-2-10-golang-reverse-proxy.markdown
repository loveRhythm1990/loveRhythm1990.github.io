---
layout:     post
title:      "golang 反向代理 reverse proxy 示例"
date:       2022-2-10 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - golang
---

一个 golang 反向代理的极简实现。关于反向代理，实际包含三部分：客户端、反向代理、服务端。其请求流程如下：

`客户端 --> 反向代理 --> 服务端`

也就是客户端先将请求发给反向代理，反向代理再将请求转发给服务端，这里反向代理也是一个服务器，监听在某个端口。具体实现如下。

### 客户端
客户端向反向代理发送请求，所以客户端需要知道反向代理的地址（这不是废话吗），依我现在的看法来看，客户端只是把服务端的地址换成了反向代理的地址，其他参数没有变。以 `http://localhost:8080/hello/def` 为例，`localhost:8080` 是反向代理的地址，`hello/def` 这个请求参数是给真正的服务端的，反向代理可以不处理。
```go
package main

import (
	"io"
	"log"
	"net/http"
	"os"
)

func main() {
	req, err := http.NewRequest("GET", "http://localhost:8080/hello/def", nil)
	if err != nil {
		log.Fatal(err)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		log.Fatal(err)
	}
	defer resp.Body.Close()

	io.Copy(os.Stdout, resp.Body)
}
```

### 反向代理
初始化反向代理需要一个参数：后端服务器的地址，也就是下面的 `http://localhost:8888`，也是唯一必须的一个，除此之前还可以搞点其他事情，比如：修改 Request、修改 Response、负载均衡、定制化错误处理等。下面我写了个修改 Response 的方法，就是在 Response 里添加了一个 header 参数，作为示例。初始化完了之后，这个代理就监听在 `8080` 端口。并将请求转发给 `8888` 端口。
```go
package main

import (
	"fmt"
	"log"
	"net/http"
	"net/http/httputil"
	"net/url"
	"time"
)
// 修改 Response
func modifyResponse() func(*http.Response) error {
	return func(resp *http.Response) error {
		fmt.Printf("%v, proxy set a header for response\n", time.Now())
		resp.Header.Set("key1", "value1")
		return nil
	}
}

func main() {
	url, err := url.Parse("http://localhost:8888")
	if err != nil {
		panic(err)
	}
	proxy := httputil.NewSingleHostReverseProxy(url)
	proxy.ModifyResponse = modifyResponse()

	http.HandleFunc("/", proxy.ServeHTTP)
	log.Fatal(http.ListenAndServe(":8080", nil))
}
```

### 服务端
服务端是真正的业务处理，其代码如下，代码比较简单，就是监听在 `8888` 端口，并且定义了一个 hello 处理方法。
```go
package main

import (
	"fmt"
	"log"
	"net/http"
	"time"

	"github.com/julienschmidt/httprouter"
)
func Hello(w http.ResponseWriter, r *http.Request, ps httprouter.Params) {
	fmt.Println("hello handler has been called in server")
	fmt.Fprintf(w, "hello, %s!\n", ps.ByName("name"))
}

func main() {
	router := httprouter.New()
	router.GET("/hello/:name", Hello)
	log.Fatal(http.ListenAndServe(":8888", router))
}
```

参考：[使用 Go 语言徒手撸一个简单的负载均衡器](https://www.infoq.cn/article/h1yh7631okqsfffuvgkt)