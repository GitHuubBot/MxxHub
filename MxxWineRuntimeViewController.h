#import <UIKit/UIKit.h>

NS_ASSUME_NONNULL_BEGIN

@interface MxxWineRuntimeViewController : UIViewController
- (instancetype)initWithExecutablePath:(NSString *)executablePath;
@property (nonatomic, copy, readonly) NSString *executablePath;
@end

NS_ASSUME_NONNULL_END
