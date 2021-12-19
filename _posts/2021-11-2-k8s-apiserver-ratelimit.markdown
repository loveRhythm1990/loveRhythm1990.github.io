---
layout:     post
title:      "Kubernetes max-requests-inflight 流控实现"
date:       2020-11-27 10:10:00
author:     "weak old dog"
header-img-credit: false
tags:
    - k8s
---

了解下，Kubernetes Apiserver 中的 `--max-mutating-requests-inflight`(Default: 200)以及 `--max-requests-inflight`(Default: 400)是怎么实现的。

代码路径为：`kubernetes/staging/src/k8s.io/apiserver/pkg/server/config.go`

Apiserver 中，handler 有众多中间件，在 http server 中间件的实现中，其参数是一个 handler，返回值也是一个 handler，但是这个返回的 handler 是对参数传进来的 handler 的一个封装，添加一些业务逻辑，最常见的包括：添加审计日志、添加监控等。等审计日志记录了，或者监控添加好了，就可以调用参数传递进来的 handler 来进行真正的业务处理。 

本示例研究的是服务端流控，那就是在真正调用参数传递进来的 handler 之前，要进行请求数的检查，这个请求数是 `inflight` 请求，也就是正在处理的请求数检查，如果超过了指定的值，则返回 `429 StatusTooManyRequests` 错误。没有超过则正常处理。

在 config.go 文件中，可以看到 Apiserver 有众多中间件
```go
func DefaultBuildHandlerChain(apiHandler http.Handler, c *Config) http.Handler {
	handler := genericapifilters.WithAuthorization(apiHandler, c.Authorization.Authorizer, c.Serializer)
	handler = genericfilters.WithMaxInFlightLimit(handler, c.MaxRequestsInFlight, c.MaxMutatingRequestsInFlight, c.LongRunningFunc)
	handler = genericapifilters.WithImpersonation(handler, c.Authorization.Authorizer, c.Serializer)
	handler = genericapifilters.WithAudit(handler, c.AuditBackend, c.AuditPolicyChecker, c.LongRunningFunc)
	failedHandler := genericapifilters.Unauthorized(c.Serializer, c.Authentication.SupportsBasicAuth)
	failedHandler = genericapifilters.WithFailedAuthenticationAudit(failedHandler, c.AuditBackend, c.AuditPolicyChecker)
	handler = genericapifilters.WithAuthentication(handler, c.Authentication.Authenticator, failedHandler, c.Authentication.APIAudiences)
	handler = genericfilters.WithCORS(handler, c.CorsAllowedOriginList, nil, nil, nil, "true")
	handler = genericfilters.WithTimeoutForNonLongRunningRequests(handler, c.LongRunningFunc, c.RequestTimeout)
	handler = genericfilters.WithWaitGroup(handler, c.LongRunningFunc, c.HandlerChainWaitGroup)
	handler = genericapifilters.WithRequestInfo(handler, c.RequestInfoResolver)
	handler = genericapifilters.WithCacheControl(handler)
	handler = genericfilters.WithPanicRecovery(handler)
	return handler
}
```
上面看到了很多跟鉴权相关的，比较眼熟的一个是 `withAudit`，一看就是添加审计日志用的，还有个 `genericfilters.WithMaxInFlightLimit`，就是流量控制了，我们看一下这个中间件的实现。

另一个需要关心的是这些 handler 的处理顺序（套娃 handler），也就是来了一个请求，哪一个 handler 最先被执行？我们可以把问题简化，对于一个中间件来说，其大致框架如下：
```go
func WithMaxInFlightLimit(handler http.Handler) http.Handler {
    
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        // before 处理逻辑
        // ... ...

        handler.ServeHTTP(w, r)
        
        // after 处理逻辑
        // ... ...
    }
}
```
也就是说 before 逻辑是早于参数传递进来的 handler 执行的，但是 after 逻辑是晚于 handler 执行的，但是另一方面 Apiserver 的 handler 一般都是 before 逻辑，**因此可以认为，越在下面的 handler 越早执行**

apiserver 流控的完整代码如下，除去跟监控相关的代码，代码也比较少。思路就是用一个 channel 作为流控队列，能写进去就正常处理，不能写进去就直接返回（当前还要写错误码以及错误信息，相当于把请求拦截了。）
```go
// WithMaxInFlightLimit limits the number of in-flight requests to buffer size of the passed in channel.
func WithMaxInFlightLimit(
	handler http.Handler,
	nonMutatingLimit int,
	mutatingLimit int,
	longRunningRequestCheck apirequest.LongRunningRequestCheck,
) http.Handler {
	startOnce.Do(startRecordingUsage)
	if nonMutatingLimit == 0 && mutatingLimit == 0 {
		return handler
    }
    
    // 声明的全局的 channel，用来作为请求的阻塞队列，初始化大小
	var nonMutatingChan chan bool
	var mutatingChan chan bool
	if nonMutatingLimit != 0 {
		nonMutatingChan = make(chan bool, nonMutatingLimit)
	}
	if mutatingLimit != 0 {
		mutatingChan = make(chan bool, mutatingLimit)
	}

    // 中间件的返回参数，也是一个 handler，封装原来的 handler
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ctx := r.Context()
		requestInfo, ok := apirequest.RequestInfoFrom(ctx)
		if !ok {
			handleError(w, r, fmt.Errorf("no RequestInfo found in context, handler chain must be wrong"))
			return
		}

		// Skip tracking long running events.
		if longRunningRequestCheck != nil && longRunningRequestCheck(r, requestInfo) {
			handler.ServeHTTP(w, r)
			return
		}

		var c chan bool
		isMutatingRequest := !nonMutatingRequestVerbs.Has(requestInfo.Verb)
		if isMutatingRequest {
			c = mutatingChan
		} else {
			c = nonMutatingChan
		}

		if c == nil { // 这里是对应的 verb 没有设置 limit（比如是 mutate 请求，但是mutatingLimit参数置为0），直接转发到原来的 http handler 处理，不做限制
			handler.ServeHTTP(w, r)
		} else {

			select {
                //尝试向阻塞队列里写数据，能写进去，表示正在处理的请求没有把 channel 打满
			case c <- true:
				var mutatingLen, readOnlyLen int
				if isMutatingRequest {
					mutatingLen = len(mutatingChan)
				} else {
					readOnlyLen = len(nonMutatingChan)
				}

				defer func() {
                    // handler 处理完了之后，从阻塞队列 channel 中读出一个元素，表示正在处理的请求少了一个
					<-c
					if isMutatingRequest {
						watermark.recordMutating(mutatingLen)
					} else {
						watermark.recordReadOnly(readOnlyLen)
					}

                }()
                // 能写进去说明当前未达到限制，可以直接处理
				handler.ServeHTTP(w, r)

			default:
                // 省去了跟监控相关的代码。

                // 下面的 tooManyRequest 就是 channel 写不进去的处理逻辑，也就是当前所有处理请求数量已经达到限制了， 这个限制就是 channel 的长度。
				tooManyRequests(r, w)
			}
		}
	})
}

// 处理请求太多了怎么办？返回错误码、header、body，直接返回
func tooManyRequests(req *http.Request, w http.ResponseWriter) {
	// Return a 429 status indicating "Too Many Requests"
	w.Header().Set("Retry-After", acquireReteyAfterTime())
	http.Error(w, "Too many requests, please try again later.", http.StatusTooManyRequests)
}

// 下面是 http.Error() 的实现
// Error replies to the request with the specified error message and HTTP code.
// It does not otherwise end the request; the caller should ensure no further
// writes are done to w.
// The error message should be plain text.
func Error(w ResponseWriter, error string, code int) {
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.WriteHeader(code)
	fmt.Fprintln(w, error)
}
```
