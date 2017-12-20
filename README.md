# Summary 

VMs can be created on ESXi directly or against vCenter

Currently work in progress and first use of Github. Fully aware it's not yet pep8 compliant but a good use case to get experience with Github and pulic repos

To do:

Script to be broken into a class to abstract creation methods into another script
Remove hardcoded variables


# create-vsphere-vm-from-yaml

Create one or many VMs from a Yaml file

## Use

./createVms.py --yaml vm-list.yaml --host 192.168.1.x
