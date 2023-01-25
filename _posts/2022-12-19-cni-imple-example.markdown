---
layout:     post
title:      "CNI 实现：以 flannel 为例解析 CNI 插件的实现"
date:       2022-12-19 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - 网络
---

在《[K8s dockershim CNI 实现解析](https://loverhythm1990.github.io/2022/12/17/k8s-cni-imple/)》文章中，我们解释了 CNI 在 K8s 侧的一些实现，包括涉及到的 dockershim 的一些数据结构、CNI 仓库 libcni 的一些数据结构，以及 CNI 调用具体插件的参数、参数传递方式等。在本文中，我们将以 flannel 为例大概介绍一下 cni 插件的实现，其实flannel实现涉及到好几个项目：
* [cni-plugin](https://github.com/flannel-io/cni-plugin)：这个就是我们要解析的项目，这个项目生成的是一个二进制文件 `flannel`，并且放到目录 `/opt/cni/bin/`目录，供kubelet 消费。但是光有这个项目是不行的，或者说这个项目做的事情比较简单。
* [flanneld](https://github.com/flannel-io/flannel)：这个是一个daemonset，运行在每个节点上，这个项目作用为：在生成每个节点的 `/run/flannel/subnet.env` 文件，供上面的 cni-plugin 消费，这个文件是根据 node.spec.podCIDR 实现的，关于这个项目本文不做详细介绍，后面希望会有文章介绍这个。
* 公共 [cni plugins](https://github.com/containernetworking/plugins)：flannel 其实把这个项目 fork 了一份[https://github.com/flannel-io/plugins](https://github.com/flannel-io/plugins)，可能做了一些修改，这里我们只介绍官方的 cni 实现，主要是两个插件`bridge`、`ipam`。


### CNI 插件实现框架
CNI 官方提供了一些库[github.com/containernetworking/cni]([github.com/containernetworking/cni)，在这些库的支持下，实现一个插件还是非常简单的，只要实现三个参数即可：`cmdAdd`、`cmdCheck`、`cmdDel`。
```go
func main() {
	skel.PluginMain(cmdAdd, cmdCheck, cmdDel, cni.All, fullVer)
}
```
低版本的 CNI 只需实现 add 和 del 就好了，下面以 flannel 为例介绍 add 和 del 的实现。首先贴上 flannel 网络的配置文件，在下面文件中，关于 flannel 的配置只有几行，首先是 `type: "flannel"`，这个是说 dockershim 在调用 cni 插件时，首先调用 flannel 这个二进制文件（plugins 是一个数组，其中的插件是顺序调用的），然后有两条 delegate 的配置，配置比较简单，但其实很多值都用了默认值，我们在其实现中将会看到。
```json
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
```
#### cmdAdd 配置网络
我们在《[K8s dockershim CNI 实现解析](https://loverhythm1990.github.io/2022/12/17/k8s-cni-imple/)》中介绍过，执行 cni 二进制文件时，有两种方式传入数据，一种是环境变量，另一种是标准输入，其中标准输入是一大包数据，包含 cni 官方的数据，自定义的数据（字段）；也可以划分为运行时数据（如网络ns、pod 名字等）或者网络配置数据。对于自定义的字段，在反序列化为 cni 官方的数据结构时会丢失，但是反序列化为自定义的数据结构就没问题了，一般都是在官方数据结构`types.NetConf`外面再包一层。

下面的`loadFlannelNetConf`方法就是反序列化标准输入为 flannel 自定义的数据结构`NetConf`，其中嵌入包含 cni 的表示数据结构`types.NetConf`
```go
// flannel 自定义的数据结构
type NetConf struct {
	// 嵌入了 cni 库的标准数据结构，网络配置
	types.NetConf

	IPAM          map[string]interface{} `json:"ipam,omitempty"` // 这个IPAM是map
	SubnetFile    string                 `json:"subnetFile"`
	DataDir       string                 `json:"dataDir"`
	Delegate      map[string]interface{} `json:"delegate"`
	// 容器运行时配置
	RuntimeConfig map[string]interface{} `json:"runtimeConfig,omitempty"`
}
// cni 库中的标准数据结构
type NetConf struct {
	CNIVersion string `json:"cniVersion,omitempty"`

	Name         string          `json:"name,omitempty"`
	Type         string          `json:"type,omitempty"`
	Capabilities map[string]bool `json:"capabilities,omitempty"`
	IPAM         IPAM            `json:"ipam,omitempty"` // 这个IPAM是个struct
	DNS          DNS             `json:"dns"`

	RawPrevResult map[string]interface{} `json:"prevResult,omitempty"`
	PrevResult    Result                 `json:"-"`
}
```
这些 `prevResult` 等字段，我们之前看过，在标准输入里都是有的。注意这里 cni的标准数据结构和 flannel自定义的数据结构有个同名但是类型不同的字段 `IPAM`，这个是不冲突的，访问内部的字段时，带上内部嵌入的变量即可。

`loadFlannelSubnetEnv` 这个方法就是读取 flanneld 项目配置的subnetenv，这个配置文件的例子如下，Network以及subnet都是用CIDR的方式表示的，在解析文件时，都用了`net.ParseCIDR`来返回用`net.IPNet`表示的网络（网络可以理解为网段，而非单个IP），例如`10.42.0.1/24`网络通过`net.ParseCIDR`表示为`ip:10.42.0.0, mask:ffffff00`：
```s
FLANNEL_NETWORK=10.42.0.0/16
FLANNEL_SUBNET=10.42.0.1/24
FLANNEL_MTU=1450
FLANNEL_IPMASQ=true
```
上面配置的作用是确定这个节点上的子网的，这个文件是由 flanneld 通过监听node资源生成，并由 flannel cni 插件消费的。IPAM 分配地址的时候要用。
```go
func cmdAdd(args *skel.CmdArgs) error {
	n, err := loadFlannelNetConf(args.StdinData)
	if err != nil {
		return err
	}
	fenv, err := loadFlannelSubnetEnv(n.SubnetFile)
	if err != nil {
		return err
	}
	if n.Delegate == nil {
		n.Delegate = make(map[string]interface{})
	} else {
		if hasKey(n.Delegate, "type") && !isString(n.Delegate["type"]) {
			return fmt.Errorf("'delegate' dictionary, if present, must have (string) 'type' field")
		}
		if hasKey(n.Delegate, "name") {
			return fmt.Errorf("'delegate' dictionary must not have 'name' field, it'll be set by flannel")
		}
		// 在使用 flannel 插件时，不能手动设置在 Delegate 中配置 ipam 字段
		// 这个字段只能由 flannel 配置
		if hasKey(n.Delegate, "ipam") {
			return fmt.Errorf("'delegate' dictionary must not have 'ipam' field, it'll be set by flannel")
		}
	}
	if n.RuntimeConfig != nil {
		n.Delegate["runtimeConfig"] = n.RuntimeConfig
	}
	return doCmdAdd(args, n, fenv)
}
```
上面代码解析了一些输入，然后校验了一下 Delegate 的配置，然后就调用 `doCmdAdd` 这个方法了。我们将部分代码解析放在了注释中。总的来说，`doCmdAdd`方法就是在配置 flannel 插件的 Delegate，委托给了 bridge 插件来执行，并进行了 ipam 相关配置。
```go
func doCmdAdd(args *skel.CmdArgs, n *NetConf, fenv *subnetEnv) error {
	n.Delegate["name"] = n.Name
	// 默认 flannel 的 delegate 配置只有两个，是没有这个 type 字段的，所以从这里看
	// flannel 是 delegate 了 bridge 插件来做网桥以及虚拟网卡的配置。
	if !hasKey(n.Delegate, "type") {
		n.Delegate["type"] = "bridge"
	}
	// ipMasq、mtu 配置先不考虑
    // 这是设置 Gateway
	if n.Delegate["type"].(string) == "bridge" {
		if !hasKey(n.Delegate, "isGateway") {
			n.Delegate["isGateway"] = true
		}
	}
	// 获取 ipam 的配置，这里主要有三个配置：
	// 1. ipam 的 type，这个type，就是二进制插件的名字，默认为 `host-local`
	// 2. ipam 的 ranges，这个就是subnetwork的网段，通过CIDR表示
	// 3. ipam 的 routes，路由，包括两部分，一部分是在 ipam 配置文件中配置的，另一部分是在 subnet配置文件里的 FLANNEL_NETWORK 配置的。
	ipam, err := getDelegateIPAM(n, fenv)
	if err != nil {
		return fmt.Errorf("failed to assemble Delegate IPAM: %w", err)
	}
	n.Delegate["ipam"] = ipam
	fmt.Fprintf(os.Stderr, "\n%#v\n", n.Delegate)

	return delegateAdd(args.ContainerID, n.DataDir, n.Delegate)
}
```
上面代码有种很多配置 ipam 的操作，注意 ipam 字段跟 type 字段是并列的，也就是跟 bridge 是并列的，可以认为 ipam 是这个 bridge 插件配置的一部分。所以这段代码结尾调用的方法`delegateAdd`方法，其实就是将插件的执行委托给了 bridge 插件。

在调用 `delegateAdd` 时，有三个参数，我们先解释一下：
* ContainerID：容器ID，比较容易理解
* DataDir: 这个是 flannel 的配置，默认为`/var/lib/cni/flannel`，是存放cni插件配置的地方，在这个目录下面有很多以容器ID命名（就是第一个参数）的文件，每个文件中，都放置了这个容器的 flannel插件的 delegate 配置，就是下面内容，通过下面内容，我们也很直观的的看到`doCmdAdd`这个方法都干了什么。下面节点中，subnet 的配置为`10.42.9.0/24`是因为当前节点的子网配置为`FLANNEL_SUBNET=10.42.9.1/24`。
```json
{
	"cniVersion": "0.3.1",
	"hairpinMode": true,
	"ipMasq": false,
	"ipam": {
		"routes": [{
			"dst": "10.42.0.0/16"
		}],
		"subnet": "10.42.9.0/24",
		"type": "host-local"
	},
	"isDefaultGateway": true,
	"isGateway": true,
	"mtu": 1450,
	"name": "cbr0",
	"type": "bridge"
}
```
* Delegate，就是 flannel插件的delegate 配置，注意这里只传递了 flannel 的 delegate 配置。

在 `delegateAdd` 方法中，主要就是调用 bridge 来配置网络了，其输入就是上面的配置文件。
```go
result, err := invoke.DelegateAdd(context.TODO(), netconf["type"].(string), netconfBytes, nil)
```

#### bridge 插件配置网卡
这个已经不属于 flannel 的范畴了，这个是 cni 官方提供的插件，其实现在[https://github.com/containernetworking/plugins](https://github.com/containernetworking/plugins)仓库中，
其实现主要有配置 `cbr0` bridge，设置 veth pair等。我们不详细介绍这部分了。

#### cmdDel 网络清理
网络清理操作相对于配置网络简单一些，我们在上面介绍`delegateAdd`的时候，flannel delegate 给 bridge 时，在`/var/lib/cni/flannel`目录生成了一个配置文件，这个配置文件就是在删除网络的时候消费的，从代码上看，也是直接把这部分数据交给了 bridge 插件。

### 自定义 cni 插件
自定义插件可以自己编写一个 binary，delegate 部分功能给已有插件比如 bridge、ipam host local等。也可以直接 fork bridge 的代码进行一点修改。在之前的一篇文章中，我们介绍过一个自定义 cni 插件的配置，如下：
```json
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
这个配置文件需要以`.conf`结尾，而不是`.conflist`，因为是一个单个插件，而不是插件列表。从这个配置看，其实就是配置了一个 bridge 插件，只不过对 bridge 插件进行了二次开发，在 ipam 部分有一些自定义的配置，很明显这些自定义的配置 cni 的标准数据结构 `types.NetConf` 是不识别的，不过不用慌，我们可以跟 flannel 一样，在 `types.NetConf` 上再包一层数据结构，然后再进行反序列化，另外我们之前也强调过，所有在配置文件中的内容，都会以 `args.StdinData` 的方式传入给插件，我们拿到这些数据之后，基本就可以根据自己的配置随便处理了。

bridge插件一般有三个功能：1）创建网桥，2）创建 veth 设备，3）委托 ipam 分配 ip。前面两个功能听上去简单，不影响我们对cni流程的认识，但是有很多实现上的细节，后面希望写一篇文章来单独介绍一些这些细节。

上面配置中，ipam 的配置是 delegate 给了 ipam 插件，在 delegate 时，将上述配置文件作为标准参数传递给 ipam 插件。ipam 的执行就是获得一个可用的 ip，并打印到标准输出，ipam 的调用者，会收集这些输出，并进行反序列化，比如，我们可以将下列数据结构返打印到标准输出：
```go
// cni库标准数据结构：types.Result
result := IPamResult{
	&IPConfig{
		ip, // string，ipam 申请到的ip
		gw, // string, ip 的网管
		[]Route{Route{"0.0.0.0/0", ""}},
	},
	nil,
	DNS{},
}
b, err := json.Marshal(result)
if err != nil {
	return err
}
// 打印到标准输出
fmt.Print(string(b))
```
实现就暂且分析到这里，也只是梳理了一个大概的流程，以理解实现流程为主。后面希望有文章分析 bridge/ipam 插件的实现细节，这个可能涉及到很多网络知识了。