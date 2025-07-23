variable "pm_api_url" {}
variable "pm_user" {}
variable "pm_password" {}
variable "pm_target_node" {
    default = "pve"
}
variable "template_name" {
    default = "fcos-template"
}
variable "storage" {
    default = "local-lvm"
}
variable "bridge" {
    default = "vmbr0"
}
variable "gateway_ip" {
    default = "192.168.254.254"
}
variable "ssh_pub_key_path" {
    default = "~/.ssh/id_ed25519_pivrob_zc.pub"
}

variable "node_count" {
  default = 3
}

variable "node_names" {
  default = ["master", "worker1", "worker2"]
}

variable "node_ips" {
  default = ["192.168.254.210", "192.168.254.211", "192.168.254.212"]
}

variable "cloudinit_files" {
  default = ["master.ign", "worker.ign", "worker.ign"]
}

# fedora-coreos-42.20250609.3.0-live-iso.x86_64.iso
