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

### 实现一个负载均衡器
[Let's Create a Simple Load Balancer With Go](https://kasvith.me/posts/lets-create-a-simple-lb-go/)，介绍了一个简单负载均衡器的实现，这个负载均衡器使用 reverse proxy 实现请求的转发。简单梳理下这个负载均衡器的实现。

#### 数据结构
数据结构：`Backend` 代表一个后端 Server，这个数据结构包含 Server 是否 Active、其反向代理等。 `ServerPool` 是所有 Server 的集合，因为在实现的负载均衡算法中，使用轮询的策略，所以用了一个字段 `current` 来统计前一个请求连接的 Server 序号，当下一个请求来临时，在 `current` 的基础上加 1 并取余就好了。
```go
// Backend 包含了一个后端 Server 的所有信息：URL、是否 Alive、反向代理
type Backend struct {
	URL          *url.URL
	Alive        bool
	mux          sync.RWMutex
	ReverseProxy *httputil.ReverseProxy
}

// ServerPool 是所有后端 Server 的集合，
type ServerPool struct {
	backends []*Backend
	current  uint64
}

// NextIndex atomically increase the counter and return an index
func (s *ServerPool) NextIndex() int {
	return int(atomic.AddUint64(&s.current, uint64(1)) % uint64(len(s.backends)))
}
```
#### 探活
探活就是测试后端服务是否正常，包括主动和被动两种方式，主动就是定时发送探活请求，被动就是让这个 Server 处理请求时，发现超时了。

主动探活实现如下，简单一点。每隔两分钟对所有 Server 探活一次，如果探活失败，就标记为 failure。
```go
func healthCheck() {
	t := time.NewTicker(time.Minute * 2)
	for {
		select {
		case <-t.C:
			log.Println("Starting health check...")
			serverPool.HealthCheck()
			log.Println("Health check completed")
		}
	}
}
// 对所有的后端进行探活
func (s *ServerPool) HealthCheck() {
	for _, b := range s.backends {
		status := "up"
		alive := isBackendAlive(b.URL)
		b.SetAlive(alive)
		if !alive {
			status = "down"
		}
		log.Printf("%s [%s]\n", b.URL, status)
	}
}
```

被动探活是依靠反向代理实现的，是在反向代理的 `ErrorHandler` 里实现的，有两种情况会触发反向代理的 `ErrorHandler`: 1）后端 Server 不可达。2）反向代理的 ModifyResponse 出现错误。在关于探活的实现中，如果某个 Server 的反向代理（一个后端 Server 有一个反向代理）请求某个后端失败，仍然使用这个代理重试几次，如果超过三次，就认为这个 Server 失败了，标记为 failure。

另外还有个地方需要注意一下，对于后端 Server 请求失败的次数，实现的负载均衡器是放在了 Request 的 context 参数里的。但是 golang 的 context 是不能通过电缆传输的（只能是进程内通信），这可以参考[Context Propagation over HTTP in Go](https://rakyll.medium.com/context-propagation-over-http-in-go-d4540996e9b0)，再扩展一点，这篇博客里提到了将 context 的值放到 header 里来实现 context 值传输的方式，如下：
```go
// 需要传输的 context key。
const requestIDHeader = "request-id"
// 自定义 Transport 来实现将 context 放到 header 中。
type Transport struct {
	Base http.RoundTripper
}
func (t *Transport) RoundTrip(r *http.Request) (*http.Response, error) {
	r = cloneReq(r) // per RoundTrip interface enforces
	// 取出 context key 对应的 context value 并放到 header 中。
	rid := request.IDFromContext(r.Context())
 	if rid != "" {
  		r.Header.Add(requestIDHeader, rid)
 	}
 	base := t.Base
 	if base == nil {
  		base = http.DefaultTransport
 	}
 return base.RoundTrip(r)
}
```
在这个负载均衡的反向代理实现中，实际处理的 Request 都是同一个本地的 Request，即 LB 自己收到的 Request，并传递给了 ReverseProxy。

#### 反向代理进行请求转发
这里直接给出反向代理的实现，定义一个反向代理的输入实际只要一个后端 Server 的 URL。这里使用反向代理只是为了转发请求。另外，这里的实现中，如果一个 client 的请求，试探了三个后端 Server 都失败了，则对这个 Client 返回错误。
```go
// TODO 根据提供的 url 生成一个代理，这个代理只是定义了错误处理的 handler
func generateProxy(serverUrl *url.URL) *httputil.ReverseProxy {
	// 使用提供的 url 初始化一个 reverse proxy
	proxy := httputil.NewSingleHostReverseProxy(serverUrl)

	proxy.ErrorHandler = func(writer http.ResponseWriter, request *http.Request, e error) {
		log.Printf("[%s] %s\n", serverUrl.Host, e.Error())
		// 从 request 的 context 里取出 value，TODO 关注下，这个 Request 从哪里产生的
		retries := GetRetryFromContext(request)
		if retries < 3 {
			// 出错了，重试，没超过 3 次
			select {
			case <-time.After(10 * time.Millisecond):
				// TODO 重新设置 context 并经请求重新发下去
				ctx := context.WithValue(request.Context(), Retry, retries+1)
				proxy.ServeHTTP(writer, request.WithContext(ctx))
			}
			return
		}

		// after 3 retries, mark this backend as down
		// TODO 超过了三次，标记这个 server 挂了
		serverPool.MarkBackendStatus(serverUrl, false)

		// if the same request routing for few attempts with different backends, increase the count
		attempts := GetAttemptsFromContext(request)
		log.Printf("%s(%s) Attempting retry %d\n", request.RemoteAddr, request.URL.Path, attempts)
		ctx := context.WithValue(request.Context(), Attempts, attempts+1)
		lb(writer, request.WithContext(ctx))
	}
	return proxy
}
```


参考：[使用 Go 语言徒手撸一个简单的负载均衡器](https://www.infoq.cn/article/h1yh7631okqsfffuvgkt)