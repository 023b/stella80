# Lecture 2: Training convolutional neural networks

## Convolutional layers

A convolutional neural network (CNN) replaces the dense weight matrix of a fully connected layer with small filters, typically $3 \times 3$, that slide across the image. Each filter is shared across every position, so a layer has few parameters and responds to the same pattern wherever it appears: translation equivariance. Stacking layers grows the receptive field, so early layers detect edges and later layers detect parts and objects. Pooling or strided convolution reduces the spatial resolution between stages.

## Training is still gradient descent

A CNN is trained exactly like any other neural network: a forward pass computes the cross-entropy loss on a mini-batch of images, backpropagation computes the gradient of the loss with respect to every filter weight, and an optimiser updates the weights. For a convolution, the backward pass is itself a convolution, which is why GPUs accelerate both directions equally well.

Typical settings for image classification are SGD with momentum 0.9, an initial learning rate of 0.1 for batch size 256, and a cosine or step learning rate schedule with a few epochs of warm-up. Adam with a learning rate around $10^{-3}$ converges faster at the start but often ends at slightly lower test accuracy on ImageNet-style tasks.

## Batch normalisation

Batch normalisation normalises each channel over the mini-batch to zero mean and unit variance, then applies a learned scale and shift. It lets very deep CNNs train with a much larger learning rate and makes them far less sensitive to weight initialisation, which reduces the vanishing gradient problem in practice. At test time the running averages of the mean and variance collected during training are used instead of batch statistics, so the batch size must not be too small during training.

## Data augmentation and overfitting

A CNN with millions of parameters overfits a small dataset quickly. Data augmentation creates new training images by random crops, horizontal flips, colour jitter and small rotations, so the network sees a slightly different version of each image every epoch. Weight decay (an L2 penalty on the weights) and dropout before the final classifier add further regularisation. Transfer learning, starting from weights pretrained on ImageNet and fine-tuning with a small learning rate, is usually the best option when labelled data is scarce.

## Common exam points

- Count the parameters of a convolutional layer: $k \times k \times C_{in} \times C_{out}$ plus $C_{out}$ biases.
- Explain why weight sharing gives translation equivariance.
- Explain what batch normalisation does at training time and at test time.
- Describe three data augmentations and why they help.
