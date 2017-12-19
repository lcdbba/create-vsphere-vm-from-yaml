#!/usr/bin/env python3

import json
import argparse
import sys, os
sys.path.insert(0, os.path.abspath('../'))
import time
import re
import pyVim
import yaml
from pyVim import connect
from pyVmomi import vim

def readYaml(yamlFile):

    return yaml.load(readFile(yamlFile))

def readFile(fileName):

    try:
      return open(fileName, 'r').read()
    except IOError as e:
      print("I/O error({0}): {1}".format(e.errno, e.strerror))

def connectHost(serverName, account, pw):

    print("connecting to host...")
    hostObj = connect.SmartConnectNoSSL('https',serverName,443,account,pw)

    return hostObj

def returnVimObject(content, vimtype, name):
    obj = None
    container = content.viewManager.CreateContainerView(content.rootFolder, vimtype, True)
    for vsphereObject in container.view:
        if vsphereObject.name == name:
            obj = vsphereObject
            break
    if not obj:
      return False
    else:
      return obj

def returnVmUuid(vmName, hostObject):

    content = hostObject.RetrieveContent()
    datacenter = content.rootFolder.childEntity[0]
    vmfolder = datacenter.vmFolder
    hosts = datacenter.hostFolder.childEntity
    resource_pool = hosts[0].resourcePool

    searcher = hostObject.content.searchIndex

    vms = datacenter.vmFolder.childEntity
  
    vmUuid = ""
    if len(vms) == 0:
      vmUuid = ""
    for vm in vms:
      if vm.name == vmName:
        vmUuid = vm.summary.config.uuid

    return vmUuid

def createVm(hostObj, vmName, vm):

    # Create VMX file options
    vmx_file = vim.vm.FileInfo(logDirectory=None,
                              snapshotDirectory=None,
                              suspendDirectory=None,
                              vmPathName="[" + "datastore1" + "]")

    # Create base config
    config = vim.vm.ConfigSpec(name=vmName,
                              numCPUs=vm["cpu"],
                              memoryMB=vm["memory"],
                              files=vmx_file,
                              guestId=vm["os"],
                              version="vmx-11")
 
    content = hostObj.RetrieveContent()
    datacenter = content.rootFolder.childEntity[0]
    vmFolder = datacenter.vmFolder
    hosts = datacenter.hostFolder.childEntity
    resource_pool = hosts[0].resourcePool

    task = vmFolder.CreateVM_Task(config=config, pool=resource_pool)

    return task

def findFreeIdeController(vm):
    for dev in vm.config.hardware.device:
        if isinstance(dev, vim.vm.device.VirtualIDEController):
            # If there are less than 2 devices attached, we can use it.
            if len(dev.device) < 2:
                return dev
    return None

def addVmCdrom(hostObj, vm):

    # Get VM object and ESXi host content
    content = hostObj.RetrieveContent()
    vmObject = returnVimObject(content, [vim.VirtualMachine], vm)
    spec = vim.vm.ConfigSpec()

    # Get the controller key
    controller = findFreeIdeController(vmObject)
    op = vim.vm.device.VirtualDeviceSpec.Operation
    
    # Define the spec
    deviceSpec = vim.vm.device.VirtualDeviceSpec()
    connectable = vim.vm.device.VirtualDevice.ConnectInfo()
    connectable.allowGuestControl = True
    connectable.startConnected = True
    backing = vim.vm.device.VirtualCdrom.RemotePassthroughBackingInfo(useAutoDetect=False)
    cdrom = vim.vm.device.VirtualCdrom()
    cdrom.controllerKey = controller.key
    cdrom.key = -1
    cdrom.connectable = connectable
    cdrom.backing = backing
    deviceSpec.operation = op.add
    deviceSpec.device = cdrom

    # Reconfigure the VM
    configSpec = vim.vm.ConfigSpec(deviceChange=[deviceSpec])
    vmObject.Reconfigure(configSpec)

    # Wait for task to complete
    time.sleep(1)

def addVmDisk(hostObj, vmName, vm):

    # Get VM object and ESXi host content
    content = hostObj.RetrieveContent()
    vmObject = returnVimObject(content, [vim.VirtualMachine], vmName)
    spec = vim.vm.ConfigSpec()

    scsiUnitNumber = 3
    pciSlotNumber = 16
    scsiBusNumber = 0
 
    for diskController in vm["diskcontrollers"]:

      storageChanges = []

      # Define the controller
      scsiCtr = vim.vm.device.VirtualDeviceSpec()
      scsiCtr.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
      if diskController["diskcontroller"] == "lsi logic parallel":
        scsiCtr.device = vim.vm.device.VirtualLsiLogicController()
      elif diskController["diskcontroller"] == "paravirtual":
        scsiCtr.device = vim.vm.device.ParaVirtualSCSIController()
      scsiCtr.device.slotInfo = vim.vm.device.VirtualDevice.PciBusSlotInfo()
      scsiCtr.device.slotInfo.pciSlotNumber = pciSlotNumber
      scsiCtr.device.controllerKey = 100
      scsiCtr.device.unitNumber = scsiUnitNumber
      scsiCtr.device.busNumber = scsiBusNumber
      scsiCtr.device.hotAddRemove = True
      scsiCtr.device.sharedBus = 'noSharing'
      scsiCtr.device.scsiCtlrUnitNumber = 7
      controller = scsiCtr.device

      # Define the disks for the controller
      unitNumber = 0
      for disk in diskController["disks"]:
        diskSizeKb = int(disk["size"]) * 1024 * 1024
        diskSpec = vim.vm.device.VirtualDeviceSpec()
        diskSpec.fileOperation = "create"
        diskSpec.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
        diskSpec.device = vim.vm.device.VirtualDisk()
        diskSpec.device.backing = vim.vm.device.VirtualDisk.FlatVer2BackingInfo()
        diskSpec.device.backing.thinProvisioned = True
        diskSpec.device.backing.diskMode = 'persistent'
        diskSpec.device.unitNumber = unitNumber
        diskSpec.device.capacityInKB = diskSizeKb
        diskSpec.device.controllerKey = controller.key
        storageChanges.append(diskSpec)
        unitNumber = unitNumber + 1
      storageChanges.append(scsiCtr)
      spec.deviceChange = storageChanges

      # Reconfigure the VM
      task = vmObject.ReconfigVM_Task(spec=spec)

      # Wait for task to complete
      time.sleep(1)

      if task.info.state == "error":
        return task
      scsiUnitNumber = scsiUnitNumber + 1
      pciSlotNumber =  pciSlotNumber + 1
      scsiBusNumber = scsiBusNumber + 1
    return task

def addVmNic(hostObj, vmName, vm):

    # Get VM object
    content = hostObj.RetrieveContent()
    vmObject = returnVimObject(content, [vim.VirtualMachine], vmName)

    # Create NIC Spec
    spec = vim.vm.ConfigSpec()
    nic_changes = []
    nic_spec = vim.vm.device.VirtualDeviceSpec()
    nic_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.add

    # Determine the NIC Type
    if vm["nictype"] == "VMXNET3":
      nic_spec.device = vim.vm.device.VirtualVmxnet3()
    elif vm["nictype"] == "E1000":
      nic_spec.device = vim.vm.device.VirtualE1000() 
    nic_spec.device.backing = vim.vm.device.VirtualEthernetCard.NetworkBackingInfo()
    nic_spec.device.backing.useAutoDetect = False
    nic_spec.device.backing.network = returnVimObject(content, [vim.Network], vm["network"])
    nic_spec.device.backing.deviceName = vm["network"]
    nic_spec.device.connectable = vim.vm.device.VirtualDevice.ConnectInfo()
    nic_spec.device.connectable.startConnected = True
    nic_spec.device.connectable.allowGuestControl = True
    nic_spec.device.connectable.connected = True
    nic_spec.device.connectable.status = 'untried'
    nic_spec.device.wakeOnLanEnabled = False
    nic_spec.device.addressType = 'generated'

    # Reconfigure the VM with the new NIC
    nic_changes.append(nic_spec)
    spec.deviceChange = nic_changes
    task = vmObject.ReconfigVM_Task(spec=spec)

    # Wait for task to complete
    time.sleep(1)
      
    return task

def returnDatastores(vcenterObj):

    content = vcenterObj.RetrieveContent()
    objView = content.viewManager.CreateContainerView(content.rootFolder, [vim.Datastore], True)
    dsList = objView.view
    objView.Destroy()
    dsTargetList = []
    dsSortedList = []

    print(dsList)

    for ds in dsList:
      dsTargetList.append(ds.info.freeSpace)
    dsTargetList = sorted(dsTargetList, reverse=True)
    # Return DSs in least used order
    for dsSize in dsTargetList:
      for ds in dsList:
        if dsSize == ds.info.freeSpace:
          dsSortedList.append(ds.name)

    return dsSortedList

def run():

    parser = build_args()
    args = parser.parse_args()

    # Get the VM type specifications
    vms = readYaml(args.yaml)#("vmSpecifications.yaml")
    # Obtain temporary host or vCenter credentials and determine storage type
    tempHostPw = "password"
    #print("Credentials for vCenter or ESXi are required:")
    #account = input("Enter in the account name: ")
    #password = input("Enter in your SU account password: ")

    # Connect to the ESX host or vCenter
    #domain = pv2pod.returnPodDomain(args.pod)
    host = args.host#"192.168.1.56"
    myHost = connectHost(host, "root", tempHostPw)

    # Get a list of all datastores
    datastores = returnDatastores(myHost)
    print(datastores)
    # Cycle through each VM in the hash and create it if it dosen't already exist then populate the hash with the uuid
    for vm in vms:

      checkVm = returnVmUuid(vm, myHost)
      if not checkVm: 
        # Create the VM
        print("Creating the VM -", vm)
        createVmTask = createVm(myHost, vm, vms[vm])
        if createVmTask.info.state == "error":
          raise Exception("VM {0} had the creation error {1}".format(vm, createVmTask.info.error.__class__.__name__))
        while createVmTask.info.state == "running":
            print("   Waiting for VM create task to finish")
            time.sleep(1)

        # Add CDROM to VM
        addVmCdrom(myHost, vm)

        # Add NIC/s to the VM
        addNicTask = addVmNic(myHost, vm, vms[vm])
        if addNicTask.info.state == "error":
          raise Exception("VM {0} had NIC creation error {1}".format(vm["hostname"], addNicTask.info.error.__class__.__name))

        # Add Disk/s to the VM
        addDiskTask = addVmDisk(myHost, vm, vms[vm])
        if createVmTask.info.state == "error":
         raise Exception("VM {0} had disk creation error {1}".format(vm["hostname"], addDiskTask.info.error.__class__.__name))
      else:
        print(vm," already exists, skipping")

    print("Disconnecting from host")
    connect.Disconnect(myHost)

def build_args():

    parser = argparse.ArgumentParser(description='Create VMs, extract the UUIDs and place these back in SINT')
    configuration_params_group = parser.add_argument_group('Configuration parameters')

    configuration_params_group.add_argument(
      '--yaml',
      help='Name of the yaml file to read from.',
      required=True
    )
    configuration_params_group.add_argument(
      '--host',
      help='Name of the temporary host to create the VMs on.',
      required=True
    )

    return parser


if __name__ == "__main__":

    run()
