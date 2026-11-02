"""Bounded pretrained-decoder fine-tuning; isolated data, NPZ checkpoints only."""
import argparse
import hashlib
import json
from pathlib import Path
import random
import sys
import time

import numpy as np
from PIL import Image
import torch
import torch.nn.functional as F

HERE = Path(__file__).resolve().parent
ENV = HERE.parent
UPSTREAM = ENV / 'qa-adain-v4/upstream'
sys.path.insert(0, str(UPSTREAM))
import net


def stats(features):
    flat = features.flatten(2)
    return flat.mean(2)[:, :, None, None], (flat.var(2, unbiased=True) + 1e-5).sqrt()[:, :, None, None]


def gram(features):
    flat = features.flatten(2)
    return torch.bmm(flat, flat.transpose(1, 2)) / flat.shape[2]


def load_npz(module, path):
    with np.load(str(path), allow_pickle=False) as archive:
        state = {name:torch.from_numpy(archive[name].copy()) for name in archive.files}
    if not all(value.dtype == torch.float32 and torch.isfinite(value).all() for value in state.values()):
        raise ValueError('Checkpoint must contain finite float32 weights')
    module.load_state_dict(state, strict=True)


def save_npz(module, path):
    state = {name:value.detach().cpu().numpy() for name,value in module.state_dict().items()}
    temporary = path.with_suffix('.npz.tmp')
    with temporary.open('wb') as stream:
        np.savez(stream, **state)
    temporary.replace(path)
    # Exercise pickle-free checkpoint readability before declaring it saved.
    with np.load(str(path), allow_pickle=False) as check:
        if set(check.files) != set(state) or not all(np.array_equal(check[name], value) for name,value in state.items()):
            raise RuntimeError('Checkpoint round-trip failed')


class Encoder(torch.nn.Module):
    def __init__(self):
        super().__init__()
        load_npz(net.vgg, UPSTREAM / 'models/vgg_normalised.npz')
        self.features = torch.nn.Sequential(*list(net.vgg.children())[:31])
        net.vgg = self.features
        self.requires_grad_(False)

    def forward(self, image):
        layers = []
        for index, layer in enumerate(self.features):
            image = layer(image)
            if index in (3, 10, 17, 30):
                layers.append(image)
        return layers


def tensor_from_pil(image):
    pixels = np.asarray(image, dtype=np.float32).transpose(2, 0, 1).copy() / 255
    return torch.from_numpy(pixels)


def resized_tensor(path, long_side):
    image = Image.open(str(path)).convert('RGB')
    ratio = long_side / max(image.size)
    size = tuple(max(8, round(value * ratio / 8) * 8) for value in image.size)
    return tensor_from_pil(image.resize(size, Image.Resampling.BICUBIC)).unsqueeze(0)


def training_files(directory):
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    files = []
    image_root = (directory / 'images').resolve()
    for row in manifest['images']:
        if row['participant'] not in ('P001', 'P002'):
            raise ValueError('Held-out participant in training manifest')
        path = (directory / row['output']).resolve()
        if path.parent != image_root or not path.name.startswith(('P001_', 'P002_')):
            raise ValueError('Training image outside approved participant/image directory')
        if hashlib.sha256(path.read_bytes()).hexdigest() != row['output_sha256']:
            raise ValueError('Training image hash mismatch: ' + path.name)
        files.append(path)
    if not files or len(files) != len(set(files)):
        raise ValueError('Training set empty or duplicated')
    return files, manifest


def random_batch(files, patch_size, batch_size, rng):
    batch = []
    for _ in range(batch_size):
        image = Image.open(str(rng.choice(files))).convert('RGB')
        scale = patch_size / min(image.size) * rng.uniform(1.0, 1.6)
        size = tuple(max(patch_size, round(value * scale)) for value in image.size)
        image = image.resize(size, Image.Resampling.BICUBIC)
        x, y = rng.randrange(image.width-patch_size+1), rng.randrange(image.height-patch_size+1)
        image = image.crop((x, y, x+patch_size, y+patch_size))
        if rng.random() < 0.5:
            image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        batch.append(tensor_from_pil(image))
    return torch.stack(batch)


def stylized_features(features, style_mean, style_std, alpha=1.0):
    mean, std = stats(features)
    return ((features-mean)/std*style_std+style_mean)*alpha + features*(1-alpha)


def save_image(result, path):
    rgb = np.rint(result[0].detach().cpu().clamp(0,1).permute(1,2,0).numpy()*255).astype(np.uint8)
    Image.fromarray(rgb).save(str(path), compress_level=1, optimize=False)


@torch.no_grad()
def snapshot(step, encoder, decoder, style_stats, heldout, output, device):
    start = time.perf_counter()
    decoder.eval()
    directory = output / ('step-{:04d}'.format(step))
    directory.mkdir()
    checkpoint = directory/'decoder.npz'
    save_npz(decoder, checkpoint)
    cases = []
    for path in heldout:
        content = resized_tensor(path, 512).to(device)
        result = decoder(stylized_features(encoder(content)[-1], *style_stats[-1]))
        target = directory/(path.stem+'.png')
        save_image(result, target)
        cases.append({'source':str(path),'output':str(target), 'shape':list(result.shape),
                      'source_is_already_filtered':'latest-capture-v3' in path.name})
    decoder.train()
    record = {'step':step,'decoder_npz':str(checkpoint),'cases':cases,'wall_s':time.perf_counter()-start}
    (directory/'snapshot.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
    print(json.dumps({'snapshot':record}),flush=True)
    return record


class FixedStyle(torch.nn.Module):
    def __init__(self, encoder, decoder, mean, std):
        super().__init__()
        self.encoder = encoder.features
        self.decoder = decoder
        self.register_buffer('style_mean', mean)
        self.register_buffer('style_std', std)

    def forward(self, content):
        features = self.encoder(content)
        return self.decoder(stylized_features(features,self.style_mean,self.style_std))


def export_model(encoder, decoder, style_stats, heldout, output):
    import onnx
    model = FixedStyle(encoder, decoder, *style_stats[-1]).cpu().eval()
    content = resized_tensor(heldout[0],512)
    target = output/'fixed-style.onnx'
    with torch.no_grad():
        expected = model(content)
        torch.onnx.export(model, content, str(target), opset_version=13, dynamo=False,
                          input_names=['content'],output_names=['stylized'],
                          dynamic_axes={'content':{2:'height',3:'width'},'stylized':{2:'height',3:'width'}},
                          do_constant_folding=True)
    onnx.checker.check_model(onnx.load(str(target)))
    np.savez(str(output/'fixed-style.reference.npz'),content=content.numpy(),result=expected.numpy())
    return {'path':str(target),'bytes':target.stat().st_size,'opset':13,'precision':'float32'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-name', required=True)
    parser.add_argument('--steps', type=int, default=600)
    parser.add_argument('--snapshot-every', type=int, default=100)
    parser.add_argument('--patch-size', type=int, default=256)
    parser.add_argument('--batch-size', type=int, default=2)
    parser.add_argument('--style-size', type=int, default=512)
    parser.add_argument('--style', type=Path, default=ENV/'qa-neural-v4/style-reference/original-xieyi-mineral-landscape-v2.png')
    parser.add_argument('--train-dir', type=Path, default=HERE/'training-data')
    parser.add_argument('--decoder', type=Path, default=UPSTREAM/'models/decoder.npz')
    parser.add_argument('--learning-rate', type=float, default=1e-5)
    parser.add_argument('--content-weight', type=float, default=1.0)
    parser.add_argument('--style-weight', type=float, default=10.0)
    parser.add_argument('--structure-weight', type=float, default=2.0)
    parser.add_argument('--gram-weight', type=float, default=0.05)
    parser.add_argument('--tv-weight', type=float, default=0.1)
    parser.add_argument('--seed', type=int, default=173)
    parser.add_argument('--device', choices=('cuda','cpu'), default='cuda')
    parser.add_argument('--export-onnx', action='store_true')
    args = parser.parse_args()
    if not 0 <= args.steps <= 1000 or not 1 <= args.snapshot_every <= 1000:
        raise ValueError('Each experiment is bounded to at most 1000 steps')
    if args.patch_size < 32 or args.patch_size % 8 or not 1 <= args.batch_size <= 4:
        raise ValueError('Use patch multiple of eight and batch between one and four')
    if Path(args.run_name).name != args.run_name:
        raise ValueError('Run name must be a directory name, not a path')
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('Requested CUDA device is unavailable; no silent CPU fallback')
    output = HERE/'runs'/args.run_name
    output.mkdir(parents=True,exist_ok=False)
    files, manifest = training_files(args.train_dir)
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.set_num_threads(4)
    rng = random.Random(args.seed)
    device = torch.device(args.device)
    encoder = Encoder().eval().to(device)
    decoder = net.decoder
    load_npz(decoder, args.decoder)
    decoder = decoder.train().to(device)
    initial_weight = next(decoder.parameters()).detach().cpu().clone()
    optimizer = torch.optim.Adam(decoder.parameters(),lr=args.learning_rate)
    with torch.no_grad():
        style_features = encoder(resized_tensor(args.style,args.style_size).to(device))
        style_stats = [stats(value) for value in style_features]
        style_grams = [gram(value) for value in style_features]
    del style_features
    heldout = [ENV/'qa-input/P003-training.jpg',ENV/'qa-still-v4/latest-capture-v3.png']
    report = {'config':{key:str(value) if isinstance(value,Path) else value for key,value in vars(args).items()},
              'torch':torch.__version__,'device':str(device),
              'gpu':torch.cuda.get_device_name(0) if device.type=='cuda' else None,
              'training_images':len(files),'training_participants':sorted(set(row['participant'] for row in manifest['images'])),
              'training_manifest_sha256':hashlib.sha256((args.train_dir/'manifest.json').read_bytes()).hexdigest(),
              'decoder_source_sha256':hashlib.sha256(args.decoder.read_bytes()).hexdigest(),
              'style_sha256':hashlib.sha256(args.style.read_bytes()).hexdigest(),
              'encoder_trainable_parameters':sum(p.numel() for p in encoder.parameters() if p.requires_grad),
              'decoder_trainable_parameters':sum(p.numel() for p in decoder.parameters() if p.requires_grad),
              'optimizer':'fresh Adam; optional decoder argument is a weight warm-start, not optimizer resume',
              'camera_used':False,'production_modified':False,
              'limitations':'72 correlated old frames, mostly one participant/room; lower loss does not establish visual quality or generalization',
              'steps':[],'snapshots':[]}
    report['snapshots'].append(snapshot(0,encoder,decoder,style_stats,heldout,output,device))
    if device.type=='cuda':
        torch.cuda.reset_peak_memory_stats()
    for step in range(1,args.steps+1):
        if device.type=='cuda':
            torch.cuda.synchronize()
        start = time.perf_counter()
        content = random_batch(files,args.patch_size,args.batch_size,rng).to(device)
        optimizer.zero_grad(set_to_none=True)
        with torch.no_grad():
            content_features = encoder(content)
            target = stylized_features(content_features[-1],*style_stats[-1])
        generated = decoder(target)
        output_features = encoder(generated)
        content_loss = F.mse_loss(output_features[-1],target)
        style_loss = generated.new_zeros(())
        gram_loss = generated.new_zeros(())
        structure_loss = generated.new_zeros(())
        for index,features in enumerate(output_features):
            mean,std = stats(features)
            style_mean,style_std = style_stats[index]
            style_loss = style_loss + F.mse_loss(mean,style_mean.expand_as(mean)) + F.mse_loss(std,style_std.expand_as(std))
            gram_loss = gram_loss + F.mse_loss(gram(features),style_grams[index].expand(features.shape[0],-1,-1))
            if index > 0:
                source_mean,source_std = stats(content_features[index])
                structure_loss = structure_loss + F.mse_loss((features-mean)/std,(content_features[index]-source_mean)/source_std)
        tv_loss = (generated[:,:,1:,:]-generated[:,:,:-1,:]).square().mean() + (generated[:,:,:,1:]-generated[:,:,:,:-1]).square().mean()
        loss = (args.content_weight*content_loss + args.style_weight*style_loss +
                args.structure_weight*structure_loss + args.gram_weight*gram_loss + args.tv_weight*tv_loss)
        if not torch.isfinite(loss):
            raise FloatingPointError('Nonfinite loss at step '+str(step))
        loss.backward()
        gradient_norm = torch.nn.utils.clip_grad_norm_(decoder.parameters(),1.0,error_if_nonfinite=True)
        optimizer.step()
        if any(parameter.grad is not None for parameter in encoder.parameters()):
            raise RuntimeError('Frozen encoder accumulated a gradient')
        if device.type=='cuda':
            torch.cuda.synchronize()
        row = {'step':step,'wall_s':time.perf_counter()-start,'loss':float(loss.detach()),
               'content':float(content_loss.detach()),'style':float(style_loss.detach()),
               'structure':float(structure_loss.detach()),'gram':float(gram_loss.detach()),
               'tv':float(tv_loss.detach()),'gradient_norm':float(gradient_norm.detach())}
        report['steps'].append(row)
        print(json.dumps(row),flush=True)
        if step % args.snapshot_every == 0 or step == args.steps:
            report['snapshots'].append(snapshot(step,encoder,decoder,style_stats,heldout,output,device))
            (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    report['first_decoder_weight_max_change'] = float((next(decoder.parameters()).detach().cpu()-initial_weight).abs().max())
    report['peak_cuda_allocated_mib'] = torch.cuda.max_memory_allocated()/1024**2 if device.type=='cuda' else None
    timings = [row['wall_s'] for row in report['steps'][1:]] or [row['wall_s'] for row in report['steps']]
    report['estimated_100_train_steps_s'] = 100*float(np.median(timings)) if timings else None
    report['visual_acceptance'] = 'not assessed automatically; inspect held-out snapshots before increasing steps'
    if args.export_onnx:
        report['onnx'] = export_model(encoder,decoder,style_stats,heldout,output)
    (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({key:report[key] for key in ('first_decoder_weight_max_change','peak_cuda_allocated_mib','estimated_100_train_steps_s','visual_acceptance')}),flush=True)


if __name__ == '__main__':
    main()
