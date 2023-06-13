---
layout:     post
title:      "一些 K8s 中 yaml 资源配置的例子"
date:       2022-11-22 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - 能效
---

一些 K8s 资源 yaml 例子，用的时候直接粘贴复制

**K8s service示例： nginx service**
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: nginx
  labels:
    app.kubernetes.io/name: proxy
spec:
  containers:
  - name: nginx
    image: nginx:stable
    ports:
      - containerPort: 80
        name: http-web-svc

---
apiVersion: v1
kind: Service
metadata:
  name: nginx-service
spec:
  selector:
    app.kubernetes.io/name: proxy
  ports:
  - name: name-of-service-port
    protocol: TCP
    port: 80
    targetPort: http-web-svc
```

**simple pod，能跑起来的最小化配置**

```yml
---
apiVersion: v1
kind: Pod
metadata:
  name: pods-simple-pod
spec:
  containers:
    - command:
        - sleep
        - "3600"
      image: busybox
      name: pods-simple-container
```

**测试网络通不通的一个 job，简单的job, curl job**

```yml
apiVersion: batch/v1
kind: Job
metadata:
  name: curl-test
  namespace: ack-cos
spec:
  completions: 1
  parallelism: 1
  template:
    metadata:
      creationTimestamp: null
    spec:
      containers:
      - command:
        - /bin/sh
        - "-c"
        - "curl www.baidu.com"
        image: curlimages/curl:latest
        imagePullPolicy: IfNotPresent
        name: main
      restartPolicy: OnFailure
```


**debug-network.yaml**

```yml
---
apiVersion: v1
kind: Pod
metadata:
  name: debug-network-pod
spec:
  containers:
    - command:
        - sleep
        - "3600"
      image: praqma/network-multitool
      name: debug-network-container

```

**dns-debug.yaml**

```yml
---
# https://kubernetes.io/docs/tasks/administer-cluster/dns-debugging-resolution/
apiVersion: v1
kind: Pod
metadata:
  name: pod-dns-debug
spec:
  containers:
    - command:
        - sleep
        - "3600"
      image: gcr.io/kubernetes-e2e-test-images/dnsutils:1.3
      name: dnsutils
```

**spec.initContainers/**

```yml
apiVersion: v1
kind: Pod
metadata:
  name: init-container-pod
spec:
  containers:
    - name: init-container-container
      image: busybox
      command: ['sh', '-c', 'echo The app is running! && sleep 3600']
  initContainers:
    - name: init-container-init-container
      image: busybox
      command: ['sh', '-c', "until nslookup pods-init-container-service.$(cat /var/run/secrets/kubernetes.io/serviceaccount/namespace).svc.cluster.local; do echo waiting for myservice; sleep 2; done"]
```

**spec.containers.lifecycle/**

```yml
apiVersion: v1
kind: Pod
metadata:
  name: lifecycle-pod
spec:
  containers:
    - image: nginx
      lifecycle:
        postStart:
          exec:
            command: ["/bin/sh", "-c", "echo Hello from the postStart handler > /usr/share/message"]
        preStop:
          exec:
            command: ["/bin/sh", "-c", "nginx -s quit; while killall -0 nginx; do sleep 1; done"]
      name: lifecycle-container
```

**resources_and_limits**

```yml
apiVersion: v1
kind: Pod
metadata:
  name: memory-request-limit-pod
spec:
  containers:
    - command: ["sleep", "3600"]
      image: busybox
      name: memory-request-limit-container
      resources:
        limits:
          memory: "200Mi"
        requests:
          memory: "100Mi"
```

**spec.affinity.nodeAffinity/**

```yml
apiVersion: v1
kind: Pod
metadata:
  name: pod-node-affinity
spec:
  affinity:
    nodeAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
          - matchExpressions:
              - key: kubernetes.io/hostname
                operator: Exists
  containers:
    - command: ["sleep", "3600"]
      name: pod-node-affinity-container
      image: busybox
```

**spec.nodeSelector/**

```yml
apiVersion: v1
kind: Pod
metadata:
  name: pod-node-selector-simple
spec:
  containers:
    - command: ["sleep", "3600"]
      image: busybox
      name: pod-node-selector-simple-container
  nodeSelector:
    node-role.kubernetes.io/master: ""
```

**spec.tolerations/**

```yml
apiVersion: v1
kind: Pod
metadata:
  name: pod-toleration
spec:
  containers:
    - command: ["sleep", "3600"]
      image: busybox
      name: pod-toleration-container
  tolerations:
    - key: ""  # empty means match all taint keys
      operator: Exists
```
