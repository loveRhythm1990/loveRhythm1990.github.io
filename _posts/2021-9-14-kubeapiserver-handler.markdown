---
layout:     post
title:      "kube-apiserver handler 注册源码分析"
date:       2021-09-14 21:43:00
author:     "weak old dog"
header-img-credit: false
tags:
    - k8s
---

之前尝试过很多次去分析 apiserver 的源代码，但是一直没有串起来，感觉这一块很难啃，在家研究了两天之后（多亏**灿都**台风，能在家办公两天），终于有点头绪了。这篇文章主要分析下 apiserver 中各种 handler 的注册流程，最初的诉求就是：*我对某个资源发起了一个请求，这个请求的代码路径是怎么样的？*，比如我 create 一个 pod，代码该怎么跟踪分析。

在[K8s Apiserver 中关于 etcd 关键链路梳理--《Kubernetes 源码剖析》](https://loverhythm1990.github.io/2021/09/13/etcd-in-k8s/)中，介绍了各种资源存储接口的实现，除了个别资源的请求需要定制化以外，一般都是调用的通用 `RegistryStore` 的方法，通用 `RegistryStore` 的代码路径为：**vendor/k8s.io/apiserver/pkg/registry/generic/registry/store.go**。对于各种资源定制化的部分，都按照其 `group` 放在了目录 **pkg/registry/<资源组>/<资源>/storage/storage.go** 中。这些资源的存储接口，其实就是资源的处理函数，只不过在调用这些接口之前，还有很多工作要做。

本文还是以 Deployment 为例进行分析。Deployment 在 **pkg/registry/apps/rest/storage_apps.go** 文件中定义了存储接口，在 **pkg/registry/apps/rest/storage_apps.go**中定义了各个资源版本的存储接口实现，不同的资源版本有：v1beta1 / v1beta2 / v1 等，以 v1beta1 为例，其定义方式为：
```go
// deployments
deploymentStorage, err := deploymentstore.NewStorage(restOptionsGetter)
if err != nil {
	return storage, err
}
storage["deployments"] = deploymentStorage.Deployment
storage["deployments/status"] = deploymentStorage.Status
storage["deployments/scale"] = deploymentStorage.Scale
```
在 Apiserver 中，汇聚所有资源存储接口实现的数据结构是 `APIGroupInfo`，其所在文件为 **vendor/k8s.io/apiserver/pkg/server/genericapiserver.go**，具体实现为：
```go
// Info about an API group.
type APIGroupInfo struct {
	PrioritizedVersions []schema.GroupVersion
	// Info about the resources in this group. It's a map from version to resource to the storage.
	VersionedResourcesStorageMap map[string]map[string]rest.Storage
	OptionsExternalVersion *schema.GroupVersion
	MetaGroupVersion *schema.GroupVersion
	Scheme *runtime.Scheme
	// 省略部分字段
}
```
我们重点看这个字段 `VersionedResourcesStorageMap map[string]map[string]rest.Storage`，这个是个二级的 map，第一个 string 类型的 key 是资源版本号，就是前面说的 v1beta1 / v1beta2 等；第二个 string 类型的 key 是资源类型，对于 Deployment 来说，有：deployment、deployment/status、deployment/scale等；value 类型 `rest.Storage` 就是具体存储方法的实现。

Apiserver 中，注册资源处理 handler 的方法是：

`func (c completedConfig) New(delegationTarget genericapiserver.DelegationTarget) (*Master, error)`

其调用链为：

`server, err := CreateServerChain(completeOptions, stopCh)`

`kubeAPIServer, err := CreateKubeAPIServer(kubeAPIServerConfig, apiExtensionsServer.GenericAPIServer, admissionPostStartHook)`

`kubeAPIServer, err := kubeAPIServerConfig.Complete().New(delegateAPIServer)`

在 `New` 方法中，对所有的资源进行了汇总：
```go
restStorageProviders := []RESTStorageProvider{
	auditregistrationrest.RESTStorageProvider{},
	authenticationrest.RESTStorageProvider{Authenticator: c.GenericConfig.Authentication.Authenticator, APIAudiences: c.GenericConfig.Authentication.APIAudiences},
	authorizationrest.RESTStorageProvider{Authorizer: c.GenericConfig.Authorization.Authorizer, RuleResolver: c.GenericConfig.RuleResolver},
	autoscalingrest.RESTStorageProvider{},
	batchrest.RESTStorageProvider{},
	certificatesrest.RESTStorageProvider{},
	coordinationrest.RESTStorageProvider{},
	discoveryrest.StorageProvider{},
	extensionsrest.RESTStorageProvider{},
	networkingrest.RESTStorageProvider{},
	noderest.RESTStorageProvider{},
	policyrest.RESTStorageProvider{},
	rbacrest.RESTStorageProvider{Authorizer: c.GenericConfig.Authorization.Authorizer},
	schedulingrest.RESTStorageProvider{},
	settingsrest.RESTStorageProvider{},
	storagerest.RESTStorageProvider{},
	// keep apps after extensions so legacy clients resolve the extensions versions of shared resource names.
	// See https://github.com/kubernetes/kubernetes/issues/42392
	// Deployment 在这里
	appsrest.RESTStorageProvider{},
	admissionregistrationrest.RESTStorageProvider{},
	eventsrest.RESTStorageProvider{TTL: c.ExtraConfig.EventTTL},
}
// 调用 InstallAPIs 进行注册处理
if err := m.InstallAPIs(c.ExtraConfig.APIResourceConfigSource, c.GenericConfig.RESTOptionsGetter, restStorageProviders...); err != nil {
	return nil, err
}
```
后面的 `InstallAPIs` 就是进行 handler 注册了。从上到下其调用链如下（以 get 请求为例），不得不说，这个调用链挺长的。

`m.InstallAPIs(c.ExtraConfig.APIResourceConfigSource, c.GenericConfig.RESTOptionsGetter, restStorageProviders...); `

`if err := m.GenericAPIServer.InstallAPIGroups(apiGroupsInfo...); err != nil {`

`if err := s.installAPIResources(APIGroupPrefix, apiGroupInfo, openAPIModels); err != nil {`

`if err := s.installAPIResources(APIGroupPrefix, apiGroupInfo, openAPIModels); err != nil {`

`if err := apiGroupVersion.InstallREST(s.Handler.GoRestfulContainer); err != nil {`

`apiResources, ws, registrationErrors := installer.Install()`

`apiResource, err := a.registerResourceHandlers(path, a.group.Storage[path], ws)`

`handler = restfulGetResourceWithOptions(getterWithOptions, reqScope, isSubresource)`

`handlers.GetResourceWithOptions(r, &scope, isSubresource)(res.ResponseWriter, req.Request)`

`getResourceHandler`

`func getResourceHandler(scope *RequestScope, getter getterFunc) http.HandlerFunc {`

我们看下 `getResourceHandler` 的最终实现，其所在代码路径为 **vendor/k8s.io/apiserver/pkg/endpoints/handlers/get.go**，在 handlers 目录下面还有其他请求的处理函数，比如 `create.go` 以及 `delete.go` 等，均是对应 verb 的处理函数。
```go
// getterFunc performs a get request with the given context and object name. The request
// may be used to deserialize an options object to pass to the getter.
type getterFunc func(ctx context.Context, name string, req *http.Request, trace *utiltrace.Trace) (runtime.Object, error)

// getResourceHandler is an HTTP handler function for get requests. It delegates to the
// passed-in getterFunc to perform the actual get.
func getResourceHandler(scope *RequestScope, getter getterFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, req *http.Request) {
		trace := utiltrace.New("Get", utiltrace.Field{"url", req.URL.Path})
		defer trace.LogIfLong(500 * time.Millisecond)

		namespace, name, err := scope.Namer.Name(req)
		if err != nil {
			scope.err(err, w, req)
			return
		}
		ctx := req.Context()
		ctx = request.WithNamespace(ctx, namespace)

		outputMediaType, _, err := negotiation.NegotiateOutputMediaType(req, scope.Serializer, scope)
		if err != nil {
			scope.err(err, w, req)
			return
		}

		result, err := getter(ctx, name, req, trace)
		if err != nil {
			scope.err(err, w, req)
			return
		}

		trace.Step("About to write a response")
		transformResponseObject(ctx, scope, trace, req, w, http.StatusOK, outputMediaType, result)
		trace.Step("Transformed response object")
	}
}
```
`getResourceHandler` 有一个 `getter` 参数，是真正执行 get 请求的函数，其实现如下，可以看出主要是通过 `r rest.GetterWithOptions` 来实现 get 请求的。
```go
func GetResourceWithOptions(r rest.GetterWithOptions, scope *RequestScope, isSubresource bool) http.HandlerFunc {
	return getResourceHandler(scope,
		func(ctx context.Context, name string, req *http.Request, trace *utiltrace.Trace) (runtime.Object, error) {
			opts, subpath, subpathKey := r.NewGetOptions()
			trace.Step("About to process Get options")
			if err := getRequestOptions(req, scope, opts, subpath, subpathKey, isSubresource); err != nil {
				err = errors.NewBadRequest(err.Error())
				return nil, err
			}
			if trace != nil {
				trace.Step("About to Get from storage")
			}
			return r.Get(ctx, name, opts)
		})
}
```
这个 `r rest.GetterWithOptions` 其实就是上面我们说的各种资源的存储接口实现，其定义在下面方法中

`func (a *APIInstaller) registerResourceHandlers(path string, storage rest.Storage, ws *restful.WebService) (*metav1.APIResource, error) {`

即：
```go
getterWithOptions, isGetterWithOptions := storage.(rest.GetterWithOptions)
```
可以看到，就是对 storage 做了一个类型转换。

总数所述，我们除了关注各种资源的存储接口实现，也要关注目录 **vendor/k8s.io/apiserver/pkg/endpoints/handlers** 目录下面的处理函数。