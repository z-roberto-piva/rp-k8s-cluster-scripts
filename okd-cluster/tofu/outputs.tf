output "vm_names" {
  value = proxmox_vm_qemu.okd_nodes[*].name
}

output "vm_ips" {
  value = var.node_ips
}
