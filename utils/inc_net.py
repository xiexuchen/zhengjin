import copy
from operator import imod
import torch
from torch import nn, norm
from convs.cifar_resnet import resnet32
from convs.resnet import resnet18, resnet34, resnet50
from convs.ucir_cifar_resnet import resnet32 as cosine_resnet32
from convs.ucir_resnet import resnet18 as cosine_resnet18
from convs.ucir_resnet import resnet34 as cosine_resnet34
from convs.ucir_resnet import resnet50 as cosine_resnet50
from convs.linears import SimpleLinear, SplitCosineLinear, CosineLinear
from convs.cifar_twobn_resnet_cbam import resnet18_cbam as resnet18_2bn_cbam
from convs.cifar_resnet_cbam import resnet18_cbam as resnet18_cbam
from convs.cifar_resnet_cbam_weight import resnet18_cbam_kw
import convs.twobn_resnet as twobn_resnet
import convs.cifar_twobn_resnet as cifar_twobn_resnet
import convs.cifar_multibn_resnet as cifar_multibn_resnet

def get_convnet(convnet_type, pretrained=False, normed=False):
    name = convnet_type.lower()

    if name == 'resnet32':
        return resnet32()
    elif name == 'resnet18':
        return resnet18(pretrained=pretrained)
    elif name == 'resnet34':
        return resnet34(pretrained=pretrained)
    elif name == 'resnet50':
        return resnet50(pretrained=pretrained)
    elif name == 'cosine_resnet18':
        return cosine_resnet18(pretrained=pretrained)
    elif name == 'cosine_resnet32':
        return cosine_resnet32()
    elif name == 'cosine_resnet34':
        return cosine_resnet34(pretrained=pretrained)
    elif name == 'cosine_resnet50':
        return cosine_resnet50(pretrained=pretrained)
    elif name == 'resnet18_2bn':
        norm_layer = twobn_resnet.MixBatchNorm2d
        attacker = twobn_resnet.PGDAttacker(num_iter=1, epsilon=1, step_size=1)
        net = twobn_resnet.resnet18(norm_layer=norm_layer, attacker=attacker)
        net.mixbn = True
        print('creat resnet18_2bn!')
        return net
    elif name == 'resnet32_2bn':
        net = cifar_twobn_resnet.resnet32(mix=True)
        print('creat resnet32_2bn!')
        return net
    elif name == 'resnet32_multibn':
        net = cifar_multibn_resnet.resnet32()
        return net
    elif name == 'resnet18_2bn_cbam':
        net = resnet18_2bn_cbam(mix=True)
        print('creat resnet18_2bn_cbam!')
        return net
    elif name == 'resnet18_cbam':
        net = resnet18_cbam(normed=normed)
        print('creat resnet18_cbam!')
        return net
    elif name == 'resnet18_cbam_kw':
        net = resnet18_cbam_kw(normed=normed)
        print('creat resnet18_cbam_kw!')
        return net
    else:
        raise NotImplementedError('Unknown type {}'.format(convnet_type))


class BaseNet(nn.Module):

    def __init__(self, convnet_type, pretrained, normed=False):
        super(BaseNet, self).__init__()

        self.convnet = get_convnet(convnet_type, pretrained, normed)
        self.fc = None

    @property
    def feature_dim(self):
        return self.convnet.out_dim

    def extract_vector(self, x):
        return self.convnet(x)['features']

    def forward(self, x):
        x = self.convnet(x)
        out = self.fc(x['features'])
        '''
        {
            'fmaps': [x_1, x_2, ..., x_n],
            'features': features
            'logits': logits
        }
        '''
        out.update(x)

        return out

    def update_fc(self, nb_classes):
        pass

    def generate_fc(self, in_dim, out_dim):
        pass

    def copy(self):
        return copy.deepcopy(self)

    def freeze(self):
        for param in self.parameters():
            param.requires_grad = False
        self.eval()

        return self


class IncrementalNet(BaseNet):

    def __init__(self, convnet_type, pretrained, gradcam=False, normed=False):
        super().__init__(convnet_type, pretrained, normed)
        self.gradcam = gradcam
        if hasattr(self, 'gradcam') and self.gradcam:
            self._gradcam_hooks = [None, None]
            self.set_gradcam_hook()

    def update_fc(self, nb_classes):
        fc = self.generate_fc(self.feature_dim, nb_classes)
        if self.fc is not None:
            nb_output = self.fc.out_features
            weight = copy.deepcopy(self.fc.weight.data)
            bias = copy.deepcopy(self.fc.bias.data)
            fc.weight.data[:nb_output] = weight
            fc.bias.data[:nb_output] = bias

        del self.fc
        self.fc = fc

    def generate_fc(self, in_dim, out_dim):
        fc = SimpleLinear(in_dim, out_dim)

        return fc

    def forward(self, x):
        x = self.convnet(x)
        out = self.fc(x['features'])
        out.update(x)
        if hasattr(self, 'gradcam') and self.gradcam:
            out['gradcam_gradients'] = self._gradcam_gradients
            out['gradcam_activations'] = self._gradcam_activations

        return out

    def unset_gradcam_hook(self):
        self._gradcam_hooks[0].remove()
        self._gradcam_hooks[1].remove()
        self._gradcam_hooks[0] = None
        self._gradcam_hooks[1] = None
        self._gradcam_gradients, self._gradcam_activations = [None], [None]

    def set_gradcam_hook(self):
        self._gradcam_gradients, self._gradcam_activations = [None], [None]

        def backward_hook(module, grad_input, grad_output):
            self._gradcam_gradients[0] = grad_output[0]
            return None

        def forward_hook(module, input, output):
            self._gradcam_activations[0] = output
            return None

        self._gradcam_hooks[0] = self.convnet.last_conv.register_backward_hook(backward_hook)
        self._gradcam_hooks[1] = self.convnet.last_conv.register_forward_hook(forward_hook)


class Twobn_IncrementalNet(BaseNet):

    def __init__(self, convnet_type, pretrained, gradcam=False):
        super().__init__(convnet_type, pretrained)
        self.gradcam = gradcam
        if hasattr(self, 'gradcam') and self.gradcam:
            self._gradcam_hooks = [None, None]
            self.set_gradcam_hook()

    def update_fc(self, nb_classes):
        fc = self.generate_fc(self.feature_dim, nb_classes)
        if self.fc is not None:
            nb_output = self.fc.out_features
            weight = copy.deepcopy(self.fc.weight.data)
            bias = copy.deepcopy(self.fc.bias.data)
            fc.weight.data[:nb_output] = weight
            fc.bias.data[:nb_output] = bias

        del self.fc
        self.fc = fc

    def generate_fc(self, in_dim, out_dim):
        fc = SimpleLinear(in_dim, out_dim)

        return fc

    def forward(self, x , labels):

        # to attack . we need current fc layer
        x , labels = self.convnet(x , labels , self.fc)
        out = self.fc(x['features'])
        out.update(x)
        if hasattr(self, 'gradcam') and self.gradcam:
            out['gradcam_gradients'] = self._gradcam_gradients
            out['gradcam_activations'] = self._gradcam_activations

        return out ,labels

    def unset_gradcam_hook(self):
        self._gradcam_hooks[0].remove()
        self._gradcam_hooks[1].remove()
        self._gradcam_hooks[0] = None
        self._gradcam_hooks[1] = None
        self._gradcam_gradients, self._gradcam_activations = [None], [None]

    def set_gradcam_hook(self):
        self._gradcam_gradients, self._gradcam_activations = [None], [None]

        def backward_hook(module, grad_input, grad_output):
            self._gradcam_gradients[0] = grad_output[0]
            return None

        def forward_hook(module, input, output):
            self._gradcam_activations[0] = output
            return None

        self._gradcam_hooks[0] = self.convnet.last_conv.register_backward_hook(backward_hook)
        self._gradcam_hooks[1] = self.convnet.last_conv.register_forward_hook(forward_hook)


class CosineIncrementalNet(BaseNet):

    def __init__(self, convnet_type, pretrained, nb_proxy=1):
        super().__init__(convnet_type, pretrained)
        self.nb_proxy = nb_proxy

    def update_fc(self, nb_classes, task_num):
        fc = self.generate_fc(self.feature_dim, nb_classes)
        if self.fc is not None:
            if task_num == 1:
                fc.fc1.weight.data = self.fc.weight.data
                fc.sigma.data = self.fc.sigma.data
            else:
                prev_out_features1 = self.fc.fc1.out_features
                fc.fc1.weight.data[:prev_out_features1] = self.fc.fc1.weight.data
                fc.fc1.weight.data[prev_out_features1:] = self.fc.fc2.weight.data
                fc.sigma.data = self.fc.sigma.data

        del self.fc
        self.fc = fc

    def generate_fc(self, in_dim, out_dim):
        if self.fc is None:
            fc = CosineLinear(in_dim, out_dim, self.nb_proxy, to_reduce=True)
        else:
            prev_out_features = self.fc.out_features // self.nb_proxy
            # prev_out_features = self.fc.out_features
            fc = SplitCosineLinear(in_dim, prev_out_features, out_dim - prev_out_features, self.nb_proxy)

        return fc


class BiasLayer(nn.Module):
    def __init__(self):
        super(BiasLayer, self).__init__()
        self.alpha = nn.Parameter(torch.ones(1, requires_grad=True))
        self.beta = nn.Parameter(torch.zeros(1, requires_grad=True))

    def forward(self, x, low_range, high_range):
        ret_x = x.clone()
        ret_x[:, low_range:high_range] = self.alpha * x[:, low_range:high_range] + self.beta
        return ret_x

    def get_params(self):
        return (self.alpha.item(), self.beta.item())


class IncrementalNetWithBias(BaseNet):
    def __init__(self, convnet_type, pretrained, bias_correction=False):
        super().__init__(convnet_type, pretrained)

        # Bias layer
        self.bias_correction = bias_correction
        self.bias_layers = nn.ModuleList([])
        self.task_sizes = []

    def forward(self, x):
        x = self.convnet(x)
        out = self.fc(x['features'])
        if self.bias_correction:
            logits = out['logits']
            for i, layer in enumerate(self.bias_layers):
                logits = layer(logits, sum(self.task_sizes[:i]), sum(self.task_sizes[:i+1]))
            out['logits'] = logits

        out.update(x)

        return out

    def update_fc(self, nb_classes):
        fc = self.generate_fc(self.feature_dim, nb_classes)
        if self.fc is not None:
            nb_output = self.fc.out_features
            weight = copy.deepcopy(self.fc.weight.data)
            bias = copy.deepcopy(self.fc.bias.data)
            fc.weight.data[:nb_output] = weight
            fc.bias.data[:nb_output] = bias

        del self.fc
        self.fc = fc

        new_task_size = nb_classes - sum(self.task_sizes)
        self.task_sizes.append(new_task_size)
        self.bias_layers.append(BiasLayer())

    def generate_fc(self, in_dim, out_dim):
        fc = SimpleLinear(in_dim, out_dim)

        return fc

    def get_bias_params(self):
        params = []
        for layer in self.bias_layers:
            params.append(layer.get_params())

        return params

    def unfreeze(self):
        for param in self.parameters():
            param.requires_grad = True


class Twobn_IncrementalNetWithBias(BaseNet):
    def __init__(self, convnet_type, pretrained, bias_correction=False):
        super().__init__(convnet_type, pretrained)

        # Bias layer
        self.bias_correction = bias_correction
        self.bias_layers = nn.ModuleList([])
        self.task_sizes = []

    def forward(self, x , labels):

        # to attack , we need current fc layer
        x, labels = self.convnet(x, labels, self.fc)
        out = self.fc(x['features'])
        if self.bias_correction:
            logits = out['logits']
            for i, layer in enumerate(self.bias_layers):
                logits = layer(logits, sum(self.task_sizes[:i]), sum(self.task_sizes[:i+1]))
            out['logits'] = logits

        out.update(x)

        return out , labels

    def update_fc(self, nb_classes):
        fc = self.generate_fc(self.feature_dim, nb_classes)
        if self.fc is not None:
            nb_output = self.fc.out_features
            weight = copy.deepcopy(self.fc.weight.data)
            bias = copy.deepcopy(self.fc.bias.data)
            fc.weight.data[:nb_output] = weight
            fc.bias.data[:nb_output] = bias

        del self.fc
        self.fc = fc

        new_task_size = nb_classes - sum(self.task_sizes)
        self.task_sizes.append(new_task_size)
        self.bias_layers.append(BiasLayer())

    def generate_fc(self, in_dim, out_dim):
        fc = SimpleLinear(in_dim, out_dim)

        return fc

    def get_bias_params(self):
        params = []
        for layer in self.bias_layers:
            params.append(layer.get_params())

        return params

    def unfreeze(self):
        for param in self.parameters():
            param.requires_grad = True


if __name__ == '__main__':

    net = IncrementalNet( 'twobn_resnet' , False )
    print(net)
