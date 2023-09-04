---
layout:     post
title:      "CNI 实现：K8s dockershim CNI 实现"
date:       2022-12-17 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - 网络
---

**文章目录**
- [网络插件初始化](#网络插件初始化)
	- [ProbeNetworkPlugins 根据配置文件生成网络插件](#probenetworkplugins-根据配置文件生成网络插件)
	- [InitNetworkPlugin](#initnetworkplugin)
- [SetupPod 配置容器网络过程](#setuppod-配置容器网络过程)
	- [buildCNIRuntimeConf 配置一个 libcni 的结构体 RumtimeConf](#buildcniruntimeconf-配置一个-libcni-的结构体-rumtimeconf)
	- [AddNetworkList](#addnetworklist)
- [ExecPluginWithResult 调用二进制插件](#execpluginwithresult-调用二进制插件)
- [附录](#附录)
	- [libcni中定义的 CNI 接口](#libcni中定义的-cni-接口)
	- [golang 反序列化的一点知识](#golang-反序列化的一点知识)

解析一下 CNI 的实现，包括网络插件如何初始化，以及调用 cni 接口配置网络等，将整个过程串联了起来。本文对应的 K8s 版本为 1.16.

### 网络插件初始化
初始化的部分可以参考 [kubelet容器运行时/CRI/CNI初始化](https://loverhythm1990.github.io/2020/06/21/kubelet-runtimeservice/)，dockershim 对于cni 的初始化是在方法 `dockershim.NewDockerService` 方法中的，在该方法中与网络相关的初始化有下面几行，其中有几个参数：
* PluginConfDir: CNI 配置目录，默认为 /etc/cni/net.d
* PluginBinDirs: CNI 二进制文件目录，默认为 /opt/cni/bin
* PluginCacheDir: 这个参数还没注意过，先忽略，默认为 /var/lib/cni/cache

下面代码都是 cni 的代码库，即 dockershim 引用 cni 实现的。
```go
cniPlugins := cni.ProbeNetworkPlugins(pluginSettings.PluginConfDir, pluginSettings.PluginCacheDir, pluginSettings.PluginBinDirs)
cniPlugins = append(cniPlugins, kubenet.NewPlugin(pluginSettings.PluginBinDirs, pluginSettings.PluginCacheDir))
netHost := &dockerNetworkHost{
	&namespaceGetter{ds},
	&portMappingGetter{ds},
	&labelAnnotationGetter{ds},
}
plug, err := network.InitNetworkPlugin(cniPlugins, pluginSettings.PluginName, netHost, pluginSettings.HairpinMode, pluginSettings.NonMasqueradeCIDR, pluginSettings.MTU)
if err != nil {
	return nil, fmt.Errorf("didn't find compatible CNI plugin with given settings %+v: %v", pluginSettings, err)
}
ds.network = network.NewPluginManager(plug)
```

#### ProbeNetworkPlugins 根据配置文件生成网络插件
我们先从 `ProbeNetworkPlugins` 开始看，这个方法的作用是遍历配置文件目录，生成一个插件列表。在`/etc/cni/net.d`目录中，有两类配置文件的配置，一类是以`.conflist`结尾，另一类是以`json`或者`.conf`，前者是一个配置文件列表，后者就是一个配置文件。从我目前的观察来看，vxlan 默认是`.conflist`文件，自定义的插件是以`.conf`结尾。其中 list 对应的结构体是 `NetworkConfigList`，并不是完全的反序列化，所以实际看到的配置文件跟这个结构体可能有些字段对不上，不过读取的配置文件的所有内容，都会以`[]byte`的方式放到这个结构体的 `Bytes` 字段。这个结构体也是定义在 cni 的包里的。

如果是`.conf`结尾，其对应的结构体为`NetworkConfig`，同样有个字段`Bytes`表示配置文件中的所有内容。
```go
type NetworkConfig struct {
	Network *types.NetConf
	Bytes   []byte
}

type NetworkConfigList struct {
	Name         string
	CNIVersion   string
	DisableCheck bool
	Plugins      []*NetworkConfig
	Bytes        []byte
}

// types.NetConf
type NetConf struct {
	CNIVersion string `json:"cniVersion,omitempty"`

	Name         string          `json:"name,omitempty"`
	Type         string          `json:"type,omitempty"`
	Capabilities map[string]bool `json:"capabilities,omitempty"`
	IPAM         IPAM            `json:"ipam,omitempty"`
	DNS          DNS             `json:"dns"`

	RawPrevResult map[string]interface{} `json:"prevResult,omitempty"`
	PrevResult    Result                 `json:"-"`
}
```
下面是两个中类型的网络插件的配置例子，注意里面有些字段是跟结构体是不对应的，
```json
// /etc/cni/net.d/10-flannel.conflist
{
  "name": "cbr0",
  "cniVersion":"0.3.1",
  "plugins": [
    {
      "type": "flannel",
      "delegate": {
        "hairpinMode": true,
        "isDefaultGateway": true
      }
    },
    {
      "type": "portmap",
      "capabilities": {
        "portMappings": true
      }
    }
  ]
}

// /etc/cni/net.d/10-netmaster.conf
{
    "cniVersion": "0.2.0",
    "name": "mynet",
    "type": "bridge",
    "bridge": "br0",
    "isGateway": false,
    "ipMasq": false,
    "hairpinMode": true,
    "ipam": {
        "type": "ipam",
        "token": "e463937d15576af58ae7a7040807c018",
        "serverURL": "http://192.168.245.112:30111"
    }
}
```

另外在 `getDefaultNetwork`(`ProbeNetworkPlugins`的调用链中) 中，还有一个 CNI 接口的实现，CNI interface 定义在 `github.com/containernetworking/cni/libcni/api.go` 这个文件中，其实现者即为`CNIConfig`，在初始化这个对象时，需要的输入参数很少，只需要一个 binDirs，`cniConfig := &libcni.CNIConfig{Path: binDirs}`。

`ProbeNetworkPlugins` 的返回结果是一个`[]network.NetworkPlugin` slice，这个 `NetworkPlugin` 是 dockershim 自定义的接口（其实现者为`cniNetworkPlugin`），类似于对 CNI接口的一个封装，虽然是一个 slice，但是只有一个元素。
```go
// NetworkPlugin is an interface to network plugins for the kubelet
type NetworkPlugin interface {
    // ... ...
	// SetUpPod is the method called after the infra container of the pod has been created but before the other containers of the pod are launched.
	SetUpPod(namespace string, name string, podSandboxID kubecontainer.ContainerID, annotations, options map[string]string) error

	// TearDownPod is the method called before a pod's infra container will be deleted
	TearDownPod(namespace string, name string, podSandboxID kubecontainer.ContainerID) error
    // ... ...
}
```
在 dockershim 的 CRI 实现中 `(ds *dockerService) RunPodSandbox` 会调用 `NetworkPlugin` 的 `SetupPod` 方法为pod配置网络。
```go
err = ds.network.SetUpPod(config.GetMetadata().Namespace, config.GetMetadata().Name, cID, config.Annotations, networkOptions)
```

#### InitNetworkPlugin
这个方法根据传入的插件名字（一般情况下，第二个参数`pluginSettings.PluginName`，也就是 kubelet 的pluginname，是`cni`），初始化特定的插件。在我们的 `ProbeNetworkPlugins` 方法中，返回的 NetworkPlugin 为 cniNetworkPlugin，这个结构体的Name()方法返回的是一个常量`cni`，所以我们这里只关心 cni 插件就好了，不用关心 kubenet。
```go
plug, err := network.InitNetworkPlugin(cniPlugins, pluginSettings.PluginName, netHost, pluginSettings.HairpinMode, pluginSettings.NonMasqueradeCIDR, pluginSettings.MTU)
```
这个看了一下，就是调用了 NetworkPlugin 的`Init` 方法，不再赘述。


### SetupPod 配置容器网络过程
从 CRI 的 RunPodSandbox 接口开始看，这个最终调用的是 NetworkPlugin 的 SetUpPod 方法，也即 `cniNetworkPlugin` 的 SetupPod 方法。前面都是一些不重要的逻辑，这个 dockershim 的 SetupPod 方法，最终调用的是 libcni 的 AddNetworkList 方法。
```go
func (plugin *cniNetworkPlugin) addToNetwork(ctx context.Context, network *cniNetwork, podName string, podNamespace string, podSandboxID kubecontainer.ContainerID, podNetnsPath string, annotations, options map[string]string) (cnitypes.Result, error) {
	rt, _ := plugin.buildCNIRuntimeConf(podName, podNamespace, podSandboxID, podNetnsPath, annotations, options)

	pdesc := podDesc(podNamespace, podName, podSandboxID)
	netConf, cniNet := network.NetworkConfig, network.CNIConfig
	// 调用 libcni 的 AddNetworkList 方法。
	res, err := cniNet.AddNetworkList(ctx, netConf, rt)
	if err != nil {
		return nil, err
	}
	return res, nil
}
```
感觉这个地方有些麻烦了，我们认真看下，首先从 buildCNIRuntimeConf 开始看。

#### buildCNIRuntimeConf 配置一个 libcni 的结构体 RumtimeConf
这个方法的输出是`libcni.RuntimeConf`，是 cni 库定义的一个结构体，其定义就是下面代码的方法体中的第一行，除了列出的两个字段，还有`CapabilityArgs`以及`CacheDir`两个字段。在下面方法中，除了初始化`libcni.RuntimeConf`这个结构体，还设置了相关 capability，总体来说代码比较简单。
```go
func (plugin *cniNetworkPlugin) buildCNIRuntimeConf(podName string, podNs string, podSandboxID kubecontainer.ContainerID, podNetnsPath string, annotations, options map[string]string) (*libcni.RuntimeConf, error) {
	rt := &libcni.RuntimeConf{
		ContainerID: podSandboxID.ID,
		NetNS:       podNetnsPath,
		IfName:      network.DefaultInterfaceName,
		CacheDir:    plugin.cacheDir,
		Args: [][2]string{
			{"IgnoreUnknown", "1"},
			{"K8S_POD_NAMESPACE", podNs},
			{"K8S_POD_NAME", podName},
			{"K8S_POD_INFRA_CONTAINER_ID", podSandboxID.ID},
		},
		// CapabilityArgs: map[string]interface{}
		// CacheDir: string
	}
    
	// 设置 portMapping、Bandwidth、dns 等相关 CapabilityArgs
	rt.CapabilityArgs = map[string]interface{}{}
	return rt, nil
}
```
这里我们再理解一下 `RuntimeConf` 的意图，从字面意思上看，封装的是运行时的配置，比如容器的 NetNs，Pod 相关信息等。区别于 `NetworkConfig` 这个主要是网络相关配置

#### AddNetworkList 
我们先分析一下这个方法的参数，
* NetworkConfigList：这个参数是在 `ProbeNetworkPlugins` -> `getDefaultCNINetwork` 中初始化的，如果在`/etc/cni/net.d`目录中配置的是一个 conflist，则包含配置文件中的所有的插件，如果配置的是一个 json 或者 conf，则只包含一个插件。
* RuntimeConf：libcni 中的 RuntimeConf 结构体。

从下面代码中可以看出，对于配置文件中的每个插件，都依次调用了 addNetwork 方法。
```go
func (c *CNIConfig) AddNetworkList(ctx context.Context, list *NetworkConfigList, rt *RuntimeConf) (types.Result, error) {
	var err error
	var result types.Result
	for _, net := range list.Plugins {
		result, err = c.addNetwork(ctx, list.Name, list.CNIVersion, net, result, rt)
		if err != nil {
			return nil, err
		}
	}
	// ... ...
	return result, nil
}
```
同样，我们先分析一下 addNetwork的参数：
* name: 对于网络配置文件是 conflist 结尾的，这个就是配置文件中的名字，如上面示例配置文件中的`cbr0`，如果配置文件是 conf，会生成一个默认的NetworkConfigList，但其名字仍然是配置文件中的名字。
* CNIVersion: 配置文件中 CNI 的版本。
* net *NetworkConfig: 这个是 `NetworkConfigList` 结构体中的插件列表，也就是一个插件，配置文件中配置的一个 plugin，
* prevResult types.Result: 上一个插件的执行结果，
* rt *RuntimeConf：libcni 中的结构体。

下面是 addNetwork 的具体实现，我们来分析一下，`FindInPath`，这个是在目录下查找插件对应的二进制执行文件的名字，其实从配置文件中就能看出来`Type`就是要执行的二进制文件的名字，
```go
func (c *CNIConfig) addNetwork(ctx context.Context, name, cniVersion string, net *NetworkConfig, prevResult types.Result, rt *RuntimeConf) (types.Result, error) {
	c.ensureExec()
	// 查找，并返回全路径
	pluginPath, err := c.exec.FindInPath(net.Network.Type, c.Path)
	if err != nil {
		return nil, err
	}
	newConf, err := buildOneConfig(name, cniVersion, net, prevResult, rt)
	if err != nil {
		return nil, err
	}
	return invoke.ExecPluginWithResult(ctx, pluginPath, newConf.Bytes, c.args("ADD", rt), c.exec)
}
```
上面代码比较复杂的就是 `buildOneConfig` 这个方法了，其参数跟 `addNetwork` 完全一致，意图是将参数这些配置整合成一个配置，都放到 libcni 的 `NetworkConfig`这个结构体中，这就有点意思了，看一下是怎么整合的。

先看 `InjectConf`，这个方法有两个参数，一个是 orig，另一个是 inject，即要把 inject 注入到 orig 中，
```go
func buildOneConfig(name, cniVersion string, orig *NetworkConfig, prevResult types.Result, rt *RuntimeConf) (*NetworkConfig, error) {
	var err error
	inject := map[string]interface{}{
		"name":       name,
		"cniVersion": cniVersion,
	}
	// Add previous plugin result
	if prevResult != nil {
		inject["prevResult"] = prevResult
	}
	// Ensure every config uses the same name and version
	orig, err = InjectConf(orig, inject)
	if err != nil {
		return nil, err
	}
	return injectRuntimeConfig(orig, rt)
}

func InjectConf(original *NetworkConfig, newValues map[string]interface{}) (*NetworkConfig, error) {
	// 先将 original.Bytes 反序列化为 map[string]interface{}，我们在第一部分中介绍过，这个 Bytes 字段包含了配置文件中
	// 的所有内容，能放的内容都放到这里了
	config := make(map[string]interface{})
	err := json.Unmarshal(original.Bytes, &config)
    
	// 将所有的新值都放到这个 Map 中去，即在全量值的基础上额外添加新值
	for key, value := range newValues {
		// ... ...
		config[key] = value
	}
    // 将这个全量的kv再序列化为字节
	newBytes, err := json.Marshal(config)
	if err != nil {
		return nil, err
	}
	return ConfFromBytes(newBytes)
}

func ConfFromBytes(bytes []byte) (*NetworkConfig, error) {
	//1. 先将全量信息放到 Bytes 字段
	conf := &NetworkConfig{Bytes: bytes}
	//2. 在将全量信息反序列化为 conf.Network 字段，反序列化过程中可能有信息丢失，但是全量信息已经在 Bytes 中了
	if err := json.Unmarshal(bytes, &conf.Network); err != nil {
		return nil, fmt.Errorf("error parsing configuration: %s", err)
	}
	if conf.Network.Type == "" {
		return nil, fmt.Errorf("error parsing configuration: missing 'type'")
	}
	return conf, nil
}
```
通过上面的代码，我们看到，通过 `buildOneConfig` 将所有配置整合成了 `NetworkConfig` 结构体中，包括运行时的配置以及网路配置，所有额外的字段都放到 `Bytes` 字段中了。在后面进行网络插件调用的时候，实际也只使用了这一个Bytes字段，NetworkConfig 中的 Network 根本没有使用。

### ExecPluginWithResult 调用二进制插件
同样我们先分析一下参数
* pluginPath string： 网络插件的二进制全路径
* netconf []byte：这是一个 byte slice，包含全量的运行时配置、网络配置等，对于网络配置包含 /etc/cni/net.d 目录中配置文件的全部数据，这个参数只使用 NetworkConfig 的 Bytes 字段。
* args CNIArgs：这个参数是一个接口，而这个接口只有一个方法 `AsEnv() []string`，即返回一个 []string，这个 slice 的每一个元素都是`"key=value"` 的格式，比如 `CNI_COMMAND=ADD`，每一个元素都要被设置为环境变量，在 addNetwork 调用 `ExecPluginWithResult`时，调用了
`c.args("ADD", rt)`方法，这个方法首先将 `CNI_COMMAND=ADD`作为环境变量，还把容器运行时的一些信息也作为环境变量。
```go
func (c *CNIConfig) args(action string, rt *RuntimeConf) *invoke.Args {
	return &invoke.Args{
		Command:     action,
		ContainerID: rt.ContainerID,
		NetNS:       rt.NetNS,
		PluginArgs:  rt.Args,
		IfName:      rt.IfName,
		Path:        strings.Join(c.Path, string(os.PathListSeparator)),
	}
}
```
> 关于 `c.args("ADD",rt)` 这里还需要说明一下，这个方法的第二个参数是 `RuntimeConf`，在这个方法中，将 RuntimeConf 的 Args 参数赋值给了 `invoke.Args` 结构体，这个结构体在调用 AsEnv() 方法时，会将 RuntimeConf.Args 作为环境变量传递 `"CNI_ARGS="+pluginArgsStr`（格式为`k1=v1;k2=v2`），在执行 cni 二进制文件时，这些作为二进制文件的环境变量。说这么多，其实就想说，这个 RuntimeConf.Args 提供了一种传递运行时自定义参数的方式，比如 pod 的一些 annotation，在 cni 的自定义实现中，这个是非常重要的。

* exec Exec: 二进制文件的执行器，这个执行器可以看成无状态的。这个默认为 `DefaultExec` 可以指定一个标准错误输出。

```go
func ExecPluginWithResult(ctx context.Context, pluginPath string, netconf []byte, args CNIArgs, exec Exec) (types.Result, error) {
	if exec == nil {
		exec = defaultExec
	}
	stdoutBytes, err := exec.ExecPlugin(ctx, pluginPath, netconf, args.AsEnv())
	if err != nil {
		return nil, err
	}
	// Plugin must return result in same version as specified in netconf
	versionDecoder := &version.ConfigDecoder{}
	confVersion, err := versionDecoder.Decode(netconf)
	if err != nil {
		return nil, err
	}

	return version.NewResult(confVersion, stdoutBytes)
}
```
上面主要是调用了 `ExecPlugin` 方法，我们看下这个方法，这个方法有三个参数：1）二进制文件全路径；2）stdinData 全量配置文件，包括容器配置、网络配置的，这部分参数最终作为执行命令的Stdin，所以插件在执行时，可以通过 stdin 拿到所有需要的数据**这一点需要注意一下，因为我看到在一些自定义插件的实现中，主要是通过 stdin 拿数据的，环境变量那部分可能主要是规范，自定义的数据还是通过 stdin**；3）需要作为环境变量的参数。
```go
func (e *RawExec) ExecPlugin(ctx context.Context, pluginPath string, stdinData []byte, environ []string) ([]byte, error) {
	stdout := &bytes.Buffer{}
	c := exec.CommandContext(ctx, pluginPath)
	c.Env = environ
	c.Stdin = bytes.NewBuffer(stdinData)
	c.Stdout = stdout
	c.Stderr = e.Stderr
	if err := c.Run(); err != nil {
		return nil, pluginErr(err, stdout.Bytes())
	}
	return stdout.Bytes(), nil
}
```
另外在执行二进制文件的时候，还指定了 command 的标准错误和标准输出，其中标准输出是写到`bytes.Buffer`中去了，因为这部分数据 cni 还有使用，但是标准错误是用的 `DefaultExec`，这个默认为 os.Stderr，因为主要是kubelet在调用，如果 kubelet 以 daemon 的形式运行在主机上，那么标准错误就是主机的标准错误。

写到这里，基本上把网络插件的执行过程梳理完了，前前后后也是花了不少功夫。

### 附录
#### libcni中定义的 CNI 接口
```go
type CNI interface {
	AddNetworkList(ctx context.Context, net *NetworkConfigList, rt *RuntimeConf) (types.Result, error)
	CheckNetworkList(ctx context.Context, net *NetworkConfigList, rt *RuntimeConf) error
	DelNetworkList(ctx context.Context, net *NetworkConfigList, rt *RuntimeConf) error
	GetNetworkListCachedResult(net *NetworkConfigList, rt *RuntimeConf) (types.Result, error)

	AddNetwork(ctx context.Context, net *NetworkConfig, rt *RuntimeConf) (types.Result, error)
	CheckNetwork(ctx context.Context, net *NetworkConfig, rt *RuntimeConf) error
	DelNetwork(ctx context.Context, net *NetworkConfig, rt *RuntimeConf) error
	GetNetworkCachedResult(net *NetworkConfig, rt *RuntimeConf) (types.Result, error)

	ValidateNetworkList(ctx context.Context, net *NetworkConfigList) ([]string, error)
	ValidateNetwork(ctx context.Context, net *NetworkConfig) ([]string, error)
}
```

#### golang 反序列化的一点知识
* 反序列化时，如果目标结构体有字段被赋值，反序列化时会覆盖原来的值
* 反序列化为其他结构体时，其他结果体如果含有源结构体不存在的字段，并且字段已被赋值，则被赋值的字段将会被保留为原值

下面例子可以说明上述问题
```go
package main
import (
	"fmt"
	"encoding/json"
)
type Foo struct {
	Str string
	Id  int
}
type CmxFoo struct {
	Str string
	Id  int
	Extra string
}
func main() {
    // f will not changed
	f := Foo{"abc", 1}
	bs, _ := json.Marshal(f)
	fmt.Println(string(bs))
    // Unmarshal will overwrite original existing field 
	b := Foo{Str: "def"}
	json.Unmarshal(bs, &b)
	fmt.Printf("%v\n", b)
    // a struct can unmarshal to another struct, not contained field will be reserved
	cf := CmxFoo{
		Extra: "extra",
	}
	json.Unmarshal(bs, &cf)
	fmt.Printf("%v\n", cf)
}
// 输出为：
// {"Str":"abc","Id":1}
// {abc 1}
// {abc 1 extra}
```
