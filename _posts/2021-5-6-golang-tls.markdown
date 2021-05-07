---
layout:     post
title:      "Golang TLS编程"
date:       2021-5-6 16:53:00
author:     "weak old dog"
header-img-credit: false
tags:
    - go
---

#### 基本概念
TLS编程涉及到一些概念，比如非对称加密、私钥、公钥、CA等，需要先理清这些概念，才能把流程打通，说句题外话，之前工作的时候这部分概念理清过，也实现了TLS编程，但是文档写在公司内网了，后来也忘记了，到现在，又花了一些时间才搞清楚。

首先是`非对称加密`，涉及到公钥和私钥，公钥是随意公布的，公钥加密的内容只有私钥才能解密，私钥加密的东西只有公钥才能解密，一般私钥保存在服务端，是严格保密的，客户端使用公钥加密内容后，发送给服务端，然后服务端使用私钥进行解密。

CA([Certificate Authority](https://en.wikipedia.org/wiki/Certificate_authority))，是一些权威机构，比如`GoDaddy`等，用来验证服务端证书是正确的，没有被篡改的。一个应用服务器想要提供https服务，需要首先产生私钥和CSR(`Certificate Signing Request`)，通过CSR向CA权威机构请求签发证书，这样这个CA就能鉴证服务端证书的真伪。CSR包含这个应用服务端的基本信息，包括：公钥、common name(服务器所在主机的FQDN)，具体可以参考[What is contained in a CSR?](https://www.sslshopper.com/what-is-a-csr-certificate-signing-request.html)

还有个概念`自签发证书`，就是签发证书不是权威机构做的，而是我们自己生成的CA签发的（其实只要openssl生成一个私钥，使用这个私钥就可以做正确的签发工作），同时，做签发证书用的的私钥（ca.key）对应的公钥(ca.crt)，一般要分发到各种设备中，比如客户端所在的主机或者浏览器中，客户端就可以用这个ca.crt来验证服务端的证书。

在TLS编程中，服务端需要自己的私钥server.key，以及用CSR请求的证书server.crt，用这两个就够了。客户端需要`ca.crt`用来验证server.crt的有效性，用这个就够了，客户端在编程时可以提供`ca.crt`，如果不提供，那么这个ca.crt需要实现被导入到主机中，或者被导入到浏览器中。

K8s文档[Certificates](https://kubernetes.io/docs/tasks/administer-cluster/certificates/)介绍了整个流程：

1. 生成2048bit的私钥，自签名CA的私钥。
```s
openssl genrsa -out ca.key 2048
```
2. 生成私钥对应的证书`ca.crt`，自签名CA的证书，也就是`Root Certificate`，这个证书需要分发到各个客户端设备中，或者在编写客户端代码的时候直接提供。
```s
openssl req -x509 -new -nodes -key ca.key -subj "/CN=${MASTER_IP}" -days 10000 -out ca.crt
```
3. 生成应用程序服务端私钥，server.key
```s
openssl genrsa -out server.key 2048
```

4. 配置CSR配置文件，这个CSR文件需要提供给CA，用来签发证书，假设该配置文件名字为：`csr.conf`。

```s
[ req ]
default_bits = 2048
prompt = no
default_md = sha256
req_extensions = req_ext
distinguished_name = dn

[ dn ]
C = <country>
ST = <state>
L = <city>
O = <organization>
OU = <organization unit>
CN = <MASTER_IP>

[ req_ext ]
subjectAltName = @alt_names

[ alt_names ]
DNS.1 = kubernetes
DNS.2 = kubernetes.default
DNS.3 = kubernetes.default.svc
DNS.4 = kubernetes.default.svc.cluster
DNS.5 = kubernetes.default.svc.cluster.local
IP.1 = <MASTER_IP>
IP.2 = <MASTER_CLUSTER_IP>

[ v3_ext ]
authorityKeyIdentifier=keyid,issuer:always
basicConstraints=CA:FALSE
keyUsage=keyEncipherment,dataEncipherment
extendedKeyUsage=serverAuth,clientAuth
subjectAltName=@alt_names
```
这里主要关注`alt_names`字段，这个是服务端程序所运行的主机域名或者IP。签发的证书只能这个IP用。

我们也可以不使用配置文件，直接使用下面命令生成csr，会通过交互的方式生成csr。
```s
openssl req -new -key server.key -out server.csr
```

5. 根据配置文件，生成CSR证书
```s
openssl req -new -key server.key -out server.csr -config csr.conf
```
6. 使用ca.key和ca.crt以及csr签发证书，生成的`server.crt`文件，就是server端启动https server时需要的。
```s
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key \
-CAcreateserial -out server.crt -days 10000 \
-extensions v3_ext -extfile csr.conf
```
7. 查看证书
```s
openssl x509  -noout -text -in ./server.crt
```

上面提到了ca.crt证书的分发，在MacOS系统中，加入到系统的命令为：
```s
sudo security add-trusted-cert -d -r trustRoot -k "/Library/Keychains/System.keychain" ca.crt
```

#### Golang TLS编程
现在证书都有了，现在开始TLS编程。另外需要说明一下，我在mac上做实验时，生成证书的方式是参考[How to Create Your Own SSL Certificate Authority for Local HTTPS Development](https://deliciousbrains.com/ssl-certificate-authority-for-local-https-development/)，k8s的文档行不通。即步骤如下：
```s
# 生成ca密钥
openssl genrsa -des3 -out myCA.key 2048
# 生成ca证书，这个就是根证书
openssl req -x509 -new -nodes -key myCA.key -sha256 -days 1825 -out myCA.pem
# 将根证书安装到mac系统中
sudo security add-trusted-cert -d -r trustRoot -k "/Library/Keychains/System.keychain" myCA.pem
# 生成服务端密钥
openssl genrsa -out dev.deliciousbrains.com.key 2048
# 生成服务端csr 
openssl req -new -key dev.deliciousbrains.com.key -out dev.deliciousbrains.com.csr
# 用ca签发证书
openssl x509 -req -in dev.deliciousbrains.com.csr -CA myCA.pem -CAkey myCA.key -CAcreateserial \
-out dev.deliciousbrains.com.crt -days 825 -sha256 -extfile dev.deliciousbrains.com.ext
```
`dev.deliciousbrains.com.ext`配置文件如下
```s

authorityKeyIdentifier=keyid,issuer
basicConstraints=CA:FALSE
keyUsage = digitalSignature, nonRepudiation, keyEncipherment, dataEncipherment
subjectAltName = @alt_names

[alt_names]
#DNS.1 = dev.deliciousbrains.com
DNS.1 = localhost
```

##### server端
在`http.Server`结构中配置`TLSConfig`之后，就不需要在`ListenAndServeTLS`中，指定密钥和证书了。
```go
package main

import (
	"crypto/tls"
	"fmt"
	"log"
	"net/http"
)

const (
	cert = "/Users/decent/tls_test/dev.deliciousbrains.com.crt"
	key = "/Users/decent/tls_test/dev.deliciousbrains.com.key"
)

func main()  {
	// generate a "Certificate" struct
	cert, _ := tls.LoadX509KeyPair(cert, key)

	// create a custom server with "TLSConfig"
	s := &http.Server{
		Addr: "localhost:9000",
		Handler: nil,
		TLSConfig: &tls.Config{
			Certificates: []tls.Certificate{cert},
		},
	}

	// handle "/" route
	http.HandleFunc("/", func(res http.ResponseWriter, request *http.Request) {
		fmt.Fprint(res, "hello custom world!")
	})

	// run server on port "9000"
	log.Fatal(s.ListenAndServeTLS("", ""))
}
```

##### client端
在client端，我们可以选择不验证服务端的证书，这个时候需要指定`InsecureSkipVerify: true`，或者我们提供自签名的证书，来验证服务端证书的有效性
```go
package main

import (
	"crypto/tls"
	"crypto/x509"
	"fmt"
	"io/ioutil"
	"log"
	"net/http"
)

func main() {
	tr := &http.Transport{
        // 可以不验证服务端证书
		//TLSClientConfig:    &tls.Config{InsecureSkipVerify: true},
        // 使用自签名CA认证
        TLSClientConfig:    &tls.Config{
			RootCAs: loadCA("/Users/decent/tls_test/myCA.pem"),
		},
	}
	client := &http.Client{Transport: tr}
	resp, err := client.Get("https://localhost:9000/")

	if err != nil {
		fmt.Println("error:", err)
		return
	}
	defer resp.Body.Close()
	body, err := ioutil.ReadAll(resp.Body)
	fmt.Println(string(body))
}

func loadCA(caFile string) *x509.CertPool {
	pool := x509.NewCertPool()

	if ca, e := ioutil.ReadFile(caFile); e != nil {
		log.Fatal("ReadFile: ", e)
	} else {
		pool.AppendCertsFromPEM(ca)
	}
	return pool
}
```

上面只是对服务端证书进行认证，如果也需要验证客户端，只与可信任的客户端通信，则也需要对客户端进行通信，方法类似，需要客户端提供证书，并在服务端配置`tls.RequireAndVerifyClientCert`，
可以参考：[verify certificate in double direction](https://gist.github.com/adisheshsm/5dc412e9a0d81316761373c2e3b283b1)

客户端配置：
```go
func createClientConfig(ca, crt, key string) (*tls.Config, error) {
	caCertPEM, err := ioutil.ReadFile(ca)
	if err != nil {
		return nil, err
	}

	roots := x509.NewCertPool()
	ok := roots.AppendCertsFromPEM(caCertPEM)
	if !ok {
		panic("failed to parse root certificate")
	}

	cert, err := tls.LoadX509KeyPair(crt, key)
	if err != nil {
		return nil, err
	}
	return &tls.Config{
		Certificates: []tls.Certificate{cert},
		RootCAs:      roots,
	}, nil
}
```
服务端配置：
```go
func createServerConfig(ca, crt, key string) (*tls.Config, error) {
	caCertPEM, err := ioutil.ReadFile(ca)
	if err != nil {
		return nil, err
	}

	roots := x509.NewCertPool()
	ok := roots.AppendCertsFromPEM(caCertPEM)
	if !ok {
		panic("failed to parse root certificate")
	}

	cert, err := tls.LoadX509KeyPair(crt, key)
	if err != nil {
		return nil, err
	}
	return &tls.Config{
		Certificates: []tls.Certificate{cert},
		ClientAuth:   tls.RequireAndVerifyClientCert,
		ClientCAs:    roots,
	}, nil
}
```

关于K8s对于客户端的认证，可以参考：[Kubernetes 中的用户与身份认证授权](https://jimmysong.io/kubernetes-handbook/guide/authentication.html)

#### 参考
[How to Create Your Own SSL Certificate Authority for Local HTTPS Development](https://deliciousbrains.com/ssl-certificate-authority-for-local-https-development/)


[What is a CSR (Certificate Signing Request)?](https://www.sslshopper.com/what-is-a-csr-certificate-signing-request.html)

[Secure HTTPS servers in Go](https://medium.com/rungo/secure-https-servers-in-go-a783008b36da)

[A brief overview of the TCP/IP model, SSL/TLS/HTTPS protocols and SSL certificates](https://medium.com/jspoint/a-brief-overview-of-the-tcp-ip-model-ssl-tls-https-protocols-and-ssl-certificates-d5a6269fe29e)