---
layout:     post
title:      "一些 K8s 中 yaml 资源配置的例子"
date:       2022-11-22 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - K8s
---

一些 K8s 资源 yaml 例子，用的时候直接粘贴复制，原地址：[https://k8s-examples.container-solutions.com/examples/Pod/Pod.html](https://k8s-examples.container-solutions.com/examples/Pod/Pod.html)

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

**simple.yaml**

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

**spec.volumes.hostPath/**

```yml
apiVersion: v1
kind: Pod
metadata:
  name: volumes-hostdir-pod
spec:
  containers:
    - command:
        - sleep
        - "3600"
      image: busybox
      name: volumes-hostdir-container
      volumeMounts:
        - mountPath: /volumes-hostdir-mount-path
          name: volumes-hostdir-volume
  volumes:
    - hostPath:
        # directory location on host
        path: /tmp
      name: volumes-hostdir-volume
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

**spec.volumes.persistentVolumeClaim/**

```yml
apiVersion: v1
kind: Pod
metadata:
  name: volume-pvc
spec:
  containers:
    - name: frontend
      image: nginx
      volumeMounts:
        - mountPath: /usr/share/nginx/html
          name: volume-pvc
  volumes:
    - name: volume-pvc
      persistentVolumeClaim:
        claimName: persitent-volume-claim
```

**privileged-simple.yaml**

```yml
apiVersion: v1
kind: Pod
metadata:
  name: privileged-simple-pod
spec:
  containers:
    - command:
        - sleep
        - "3600"
      image: busybox
      name: privileged-simple-pod
      securityContext:
        privileged: true
```

**spec.containers.imagePullPolicy/**

```yml
# imagePullPolicies: IfNotPresent, Always
---
apiVersion: v1
kind: Pod
metadata:
  name: pods-image-pull-policy-pod
spec:
  containers:
    - command:
        - sleep
        - "3600"
      image: busybox
      imagePullPolicy: IfNotPresent
      name: pods-image-pull-policy-container
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