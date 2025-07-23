terraform {
  required_providers {
    proxmox = {
      source  = "Telmate/proxmox"
      # version = "2.9.14"
      version = "3.0.2-rc03"
    }
  }
}

provider "proxmox" {
  pm_api_url      = var.pm_api_url
  pm_user         = var.pm_user
  pm_password     = var.pm_password
  pm_tls_insecure = true
}

resource "proxmox_vm_qemu" "okd_nodes" {
  count       = var.node_count
  name        = "okd-${var.node_names[count.index]}"
  target_node = var.pm_target_node
  clone       = var.template_name
  full_clone  = true

  cores  = 2
  memory = 4096

  disk {
    size    = "40G"
    type    = "scsi"
    storage = var.storage
    slot    = 0
  }

  network {
    id = 1
    model  = "virtio"
    bridge = var.bridge
  }

  os_type     = "cloud-init"
  ciuser      = "core"
  sshkeys     = file(var.ssh_pub_key_path)
  ipconfig0   = "ip=${var.node_ips[count.index]}/24,gw=${var.gateway_ip}"

  cicustom = "user=local:snippets/${var.cloudinit_files[count.index]}"
}


