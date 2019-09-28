import torch
from torch import nn
import torch.nn.functional as F

from typing import Union, List


class InceptionModel(nn.Module):
    """
    If a bottleneck channel is 0, then no bottleneck channel will be used
    """

    @staticmethod
    def _expand_to_blocks(value: Union[int, bool, List[int], List[bool]],
                          num_blocks: int) -> Union[List[int], List[bool]]:
        if isinstance(value, list):
            assert len(value) == num_blocks, \
                f'Length of inputs lists must be the same as num blocks, ' \
                f'expected length {num_blocks}, got {len(value)}'
        else:
            value = [value] * num_blocks
        return value

    def __init__(self, num_blocks: int, in_channels: int, out_channels: Union[List[int], int],
                 bottleneck_channels: Union[List[int], int], kernel_sizes: Union[List[int], int],
                 use_residuals: Union[List[bool], bool, str] = 'default', num_pred_classes: int = 1
                 ) -> None:
        super().__init__()

        channels = [in_channels] + self._expand_to_blocks(out_channels, num_blocks)
        bottleneck_channels = self._expand_to_blocks(bottleneck_channels, num_blocks)
        kernel_sizes = self._expand_to_blocks(kernel_sizes, num_blocks)
        if use_residuals == 'default':
            use_residuals = [True if i % 3 == 2 else False for i in range(num_blocks)]
        use_residuals = self._expand_to_blocks(use_residuals, num_blocks)

        self.blocks = nn.Sequential(*[
            InceptionBlock(in_channels=channels[i], out_channels=channels[i + 1],
                           residual=use_residuals[i], bottleneck_channels=bottleneck_channels[i],
                           kernel_size=kernel_sizes[i]) for i in range(num_blocks)
        ])

        # a global average pooling (i.e. mean of the time dimension) is why
        # in_features=channels[-1]
        self.linear = nn.Linear(in_features=channels[-1], out_features=num_pred_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.blocks(x).mean(dim=-1)  # the mean is the global average pooling
        return self.linear(x)


class InceptionBlock(nn.Module):
    """An inception block consists of an (optional) bottleneck, followed
    by 3 conv1d layers. Optionally residual
    """

    def __init__(self, in_channels: int, out_channels: int,
                 residual: bool, stride: int = 1, bottleneck_channels: int = 32,
                 kernel_size: int = 41) -> None:
        super().__init__()

        self.use_bottleneck = bottleneck_channels > 0
        if self.use_bottleneck:
            self.bottleneck = Conv1dSamePadding(in_channels, bottleneck_channels, kernel_size=1,
                                                bias=False)
        kernel_size_s = [kernel_size // (2 ** i) for i in range(3)]
        channels = [bottleneck_channels] + [out_channels] * 3

        self.conv_layers = nn.Sequential(*[
            Conv1dSamePadding(in_channels=channels[i], out_channels=channels[i + 1],
                              kernel_size=kernel_size_s[i], stride=stride, bias=False)
            for i in range(len(kernel_size_s))
        ])

        self.batchnorm = nn.BatchNorm1d(num_features=channels[-1])
        self.relu = nn.ReLU()

        self.use_residual = residual
        if residual:
            self.residual = nn.Sequential(*[
                Conv1dSamePadding(in_channels=in_channels, out_channels=out_channels,
                                  kernel_size=1, stride=stride, bias=False),
                nn.BatchNorm1d(out_channels),
                nn.ReLU()
            ])

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        org_x = x
        if self.use_bottleneck:
            x = self.bottleneck(x)
        x = self.conv_layers(x)

        if self.use_residual:
            x = x + self.residual(org_x)
        return x


class Conv1dSamePadding(nn.Conv1d):
    """Represents the "Same" padding functionality from Tensorflow.
    See: https://github.com/pytorch/pytorch/issues/3867
    This solution is mostly copied from
    https://github.com/pytorch/pytorch/issues/3867#issuecomment-349279036
    Note that the padding argument in the initializer doesn't do anything now
    """
    def forward(self, input):
        return conv1d_same_padding(input, self.weight, self.bias, self.stride,
                                   self.dilation, self.groups)


def conv1d_same_padding(input, weight, bias=None, stride=1, dilation=1, groups=1):
    # stride and dilation are expected to be tuples.

    # first, we'll figure out how much padding is necessary for the rows
    input_length = input.size(1)
    filter_length = weight.size(1)
    effective_filter_size_length = (filter_length - 1) * dilation[0] + 1
    out_length = (input_length + stride[0] - 1) // stride[0]
    padding_length = max(0, (out_length - 1) * stride[0] + effective_filter_size_length - input_length)
    length_odd = (padding_length % 2 != 0)

    if length_odd:
        input = F.pad(input, [0, int(length_odd)])

    return F.conv1d(input, weight, bias, stride,
                    padding=padding_length // 2,
                    dilation=dilation, groups=groups)