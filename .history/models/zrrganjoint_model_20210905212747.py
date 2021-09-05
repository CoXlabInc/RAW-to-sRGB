import torch
from .base_model import BaseModel
from . import networks as N
import torch.nn as nn
import torch.optim as optim
from . import losses as L
from . import ispjoint_model
from . import pwc_net
from util.util import get_coord


class ZRRGANModel(BaseModel):
	@staticmethod
	def modify_commandline_options(parser, is_train=True):
		return parser

	def __init__(self, opt):
		super(ZRRGANModel, self).__init__(opt)
		
		self.opt = opt
		self.loss_names = ['GCMModel_L1', 'LiteISPNet_L1', 'LiteISPNet_SSIM', 'LiteISPNet_VGG',, 'Total', 'Total_D']
		self.visual_names = ['dslr_warp', 'dslr_mask', 'data_out', 'AlignNet_out']
		
		self.model_names = ['ISPNet', 'AlignNet', 'Discriminator'] # will rename in subclasses
		self.optimizer_names = ['ISPNet_optimizer_%s' % opt.optimizer,
								'AlignNet_optimizer_%s' % opt.optimizer,
								'Discriminator_optimizer_%s' % opt.optimizer]

		isp = ispjoint_model.ISPNet(opt)
		self.netISPNet= N.init_net(isp, opt.init_type, opt.init_gain, opt.gpu_ids)

		align = ispjoint_model.AlignNet(opt)
		self.netAlignNet = N.init_net(align, opt.init_type, opt.init_gain, opt.gpu_ids)

		pwcnet = pwc_net.PWCNET()
		self.netPWCNET = N.init_net(pwcnet, opt.init_type, opt.init_gain, opt.gpu_ids)
		self.set_requires_grad(self.netPWCNET, requires_grad=False)

		discriminator = Discriminator()
		self.netDiscriminator = N.init_net(discriminator, opt.init_type, opt.init_gain, opt.gpu_ids)

		if self.opt.isTrain:
			self.optimizer_ISPNet = optim.Adam(self.netISPNet.parameters(),
								lr=opt.lr,
								betas=(opt.beta1, opt.beta2),
								weight_decay=opt.weight_decay)
			self.optimizer_AlignNet = optim.Adam(self.netAlignNet.parameters(),
										  lr=opt.lr,
										  betas=(opt.beta1, opt.beta2),
										  weight_decay=opt.weight_decay)
			self.optimizer_D = optim.Adam(self.netDiscriminator.parameters(),
										  lr=opt.lr,
										  betas=(opt.beta1, opt.beta2),
										  weight_decay=opt.weight_decay)
			self.optimizers = [self.optimizer_ISPNet, self.optimizer_AlignNet, self.optimizer_D]
			
			self.criterionL1 = N.init_net(L.L1Loss(), gpu_ids=opt.gpu_ids)
			self.criterionSSIM = N.init_net(L.SSIMLoss(), gpu_ids=opt.gpu_ids)
			self.criterionVGG = N.init_net(L.VGGLoss(), gpu_ids=opt.gpu_ids)
			self.criterionGAN = N.init_net(L.GANLoss(), gpu_ids=opt.gpu_ids)

		self.isp_coord = {}

	def set_input(self, input):
		self.data_raw = input['raw'].to(self.device)
		self.data_raw_demosaic = input['raw_demosaic'].to(self.device)
		self.data_dslr = input['dslr'].to(self.device)
		self.align_coord = input['coord'].to(self.device)
		self.image_paths = input['fname']
	
	def forward(self):
		self.AlignNet_out = self.netAlignNet(self.data_raw_demosaic, self.data_dslr, self.align_coord)
		self.dslr_warp, self.dslr_mask = \
			self.get_backwarp(self.AlignNet_out, self.data_dslr, self.netPWCNET)
		
		N, C, H, W = self.data_raw.shape
		index = str(self.data_raw.shape) + '_' + str(self.data_raw.device)
		if index not in self.isp_coord:
			isp_coord = get_coord(H=H, W=W)
			isp_coord = np.expand_dims(isp_coord, axis=0)
			isp_coord = np.tile(isp_coord, (N, 1, 1, 1))
			# print(isp_coord.shape)
			self.isp_coord[index] = torch.from_numpy(isp_coord).to(self.data_raw.device)
		
		self.data_out = self.netISPNet(self.data_raw, self.isp_coord[index])
		
		if self.isTrain:
			self.AlignNet_out = self.AlignNet_out * self.dslr_mask
			self.data_out = self.data_out * self.dslr_mask
		else:
			self.dslr_warp, self.dslr_mask = \
			    self.get_backwarp(self.data_out, self.data_dslr, self.netPWCNET)

	def backward_D(self):
		predict_fake = self.netDiscriminator(self.data_out.detach())
		lossGAN_fake = self.criterionGAN(predict_fake, False).mean()

		predict_real = self.netDiscriminator(self.dslr_warp)
		lossGAN_real = self.criterionGAN(predict_real, True).mean()

		self.loss_Total_D = 0.5 * (lossGAN_fake + lossGAN_real)
		self.loss_Total_D.backward()

	def backward_G(self):
		predict_fake = self.netDiscriminator(self.data_out)
		# self.data_out = self.data_out * self.dslr_mask

		self.loss_AlignNet_L1 = self.criterionL1(self.AlignNet_out, self.dslr_warp).mean()
		self.loss_ISPNet_L1 = self.criterionL1(self.data_out, self.dslr_warp).mean()
		self.loss_ISPNet_SSIM = 1 - self.criterionSSIM(self.data_out, self.dslr_warp).mean()
		self.loss_ISPNet_VGG = self.criterionVGG(self.data_out, self.dslr_warp).mean()
		self.loss_ISPNet_GAN = self.criterionGAN(predict_fake, True).mean()
		self.loss_Total = self.loss_AlignNet_L1 + self.loss_ISPNet_L1 + self.loss_ISPNet_VGG \
		                  + self.loss_ISPNet_SSIM * 0.15 + self.loss_ISPNet_GAN * 0.01
		self.loss_Total.backward()

	def optimize_parameters(self):
		self.forward()
		# update D
		self.set_requires_grad(self.netDiscriminator, True)
		self.optimizer_D.zero_grad()
		self.backward_D()
		self.optimizer_D.step()
		# update G
		self.set_requires_grad(self.netDiscriminator, False)
		self.optimizer_ISPNet.zero_grad()
		self.optimizer_AlignNet.zero_grad()
		self.backward_G()
		self.optimizer_ISPNet.step()
		self.optimizer_AlignNet.step()

class Discriminator(nn.Module): # LAST CHANGE CONV & CHANGE PLACE
	"""Defines a PatchGAN discriminator"""
	def __init__(self, input_nc=3, ndf=64, n_layers=3, norm_layer=nn.BatchNorm2d):
		"""Construct a PatchGAN discriminator
		Parameters:
			input_nc (int)  -- the number of channels in input images
			ndf (int)       -- the number of filters in the last conv layer
			n_layers (int)  -- the number of conv layers in the discriminator
			norm_layer      -- normalization layer
		"""
		super(Discriminator, self).__init__()
		use_bias = False

		kw = 4
		padw = 1
		sequence = [nn.Conv2d(input_nc, ndf, kernel_size=kw, stride=2, padding=padw), nn.LeakyReLU(0.2, True)]
		nf_mult = 1
		nf_mult_prev = 1
		for n in range(1, n_layers):  # gradually increase the number of filters
			nf_mult_prev = nf_mult
			nf_mult = min(2 ** n, 8)
			sequence += [
				nn.Conv2d(ndf * nf_mult_prev, ndf * nf_mult, kernel_size=kw, stride=2, padding=padw, bias=use_bias),
				norm_layer(ndf * nf_mult),
				nn.LeakyReLU(0.2, True)
			]

		nf_mult_prev = nf_mult
		nf_mult = min(2 ** n_layers, 8)
		sequence += [
			nn.Conv2d(ndf * nf_mult_prev, ndf * nf_mult, kernel_size=kw, stride=1, padding=padw, bias=use_bias),
			norm_layer(ndf * nf_mult),
			nn.LeakyReLU(0.2, True)
		]

		sequence += [nn.Conv2d(ndf * nf_mult, 1, kernel_size=kw, stride=1, padding=padw)]  # output 1 channel prediction map
		self.model = nn.Sequential(*sequence)

	def forward(self, input):
		return self.model(input)
